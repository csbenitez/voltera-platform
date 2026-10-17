package main

import (
	"context"
	"errors"
	"fmt"
	"math/rand/v2"
	"sort"
	"sync"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"
	"go.opentelemetry.io/otel/trace"

	"github.com/csbenitez/voltera-eda/internal/domain"
	"github.com/csbenitez/voltera-eda/internal/platform"
)

// Escenarios de carga disponibles.
const (
	ScConstant = "constante"
	ScRamp     = "rampa"
	ScBurst    = "rafaga"
	ScKnative  = "cruce-knative"
	ScSoak     = "soak"
)

// Params describe una corrida. Los campos en cero toman el valor por defecto del escenario.
type Params struct {
	Scenario string  `json:"scenario"`
	N        int     `json:"n"`        // tope de mensajes (0 = sin tope; entonces manda duration)
	Duration int     `json:"duration"` // segundos (0 = sin tope; entonces manda n)
	Rate     int     `json:"rate"`     // msg/s (inicial en rampa)
	RateTo   int     `json:"rate_to"`  // msg/s final de la rampa
	Burst    int     `json:"burst"`    // mensajes por ráfaga
	Every    float64 `json:"every"`    // segundos entre ráfagas
	Meters   int     `json:"meters"`
	Foreign  float64 `json:"foreign"` // fracción 0..1 de medidores de la otra región
}

// Preset es lo que el dashboard ofrece como punto de partida.
type Preset struct {
	Name        string `json:"name"`
	Description string `json:"description"`
	Params      Params `json:"params"`
}

func Presets() []Preset {
	return []Preset{
		{ScConstant, "Tasa fija: N mensajes a rate msg/s.", Params{Scenario: ScConstant, N: 200, Rate: 50, Meters: 40}},
		{ScRamp, "Rampa lineal de rate a rate_to durante duration s (busca el punto de saturación).", Params{Scenario: ScRamp, Duration: 60, Rate: 10, RateTo: 200, Meters: 40}},
		{ScBurst, "Ráfagas de burst mensajes cada every s (prueba el escalado KEDA del gateway).", Params{Scenario: ScBurst, Duration: 60, Burst: 200, Every: 10, Meters: 40}},
		{ScKnative, "Parte de los medidores son de la otra región: ejercita onprem → Knative → nube.", Params{Scenario: ScKnative, N: 300, Rate: 30, Meters: 40, Foreign: 0.3}},
		{ScSoak, "Tasa baja y sostenida varios minutos (estabilidad, duplicados, lag).", Params{Scenario: ScSoak, Duration: 600, Rate: 5, Meters: 40}},
	}
}

// normalize aplica defaults y valida.
func (p *Params) normalize() error {
	switch p.Scenario {
	case "":
		p.Scenario = ScConstant
	case ScConstant, ScRamp, ScBurst, ScKnative, ScSoak:
	default:
		return fmt.Errorf("escenario desconocido %q", p.Scenario)
	}
	if p.Rate <= 0 {
		p.Rate = 50
	}
	if p.Meters <= 0 {
		p.Meters = 40
	}
	if p.Foreign < 0 || p.Foreign > 1 {
		return errors.New("foreign debe estar entre 0 y 1")
	}
	if p.Scenario == ScKnative && p.Foreign == 0 {
		p.Foreign = 0.3
	}
	switch p.Scenario {
	case ScRamp:
		if p.RateTo <= 0 {
			p.RateTo = p.Rate * 10
		}
		if p.Duration <= 0 {
			p.Duration = 60
		}
	case ScBurst:
		if p.Burst <= 0 {
			p.Burst = 100
		}
		if p.Every <= 0 {
			p.Every = 10
		}
		if p.N <= 0 && p.Duration <= 0 {
			p.Duration = 60
		}
	}
	if p.N <= 0 && p.Duration <= 0 {
		p.N = 100
	}
	if p.N > 5_000_000 || p.Rate > 20_000 || p.RateTo > 20_000 || p.Burst > 100_000 {
		return errors.New("parámetros fuera de rango")
	}
	return nil
}

// delayAfter devuelve la pausa tras enviar el mensaje i (0-based) con elapsed transcurrido.
func (p Params) delayAfter(i int, elapsed time.Duration) time.Duration {
	switch p.Scenario {
	case ScBurst:
		if (i+1)%p.Burst == 0 {
			return time.Duration(p.Every * float64(time.Second))
		}
		return 0
	case ScRamp:
		frac := min(1, elapsed.Seconds()/float64(p.Duration))
		r := float64(p.Rate) + float64(p.RateTo-p.Rate)*frac
		return time.Duration(float64(time.Second) / max(1, r))
	}
	return time.Second / time.Duration(max(1, p.Rate))
}

func (p Params) done(i int, elapsed time.Duration) bool {
	return (p.N > 0 && i >= p.N) || (p.Duration > 0 && elapsed >= time.Duration(p.Duration)*time.Second)
}

// Stats acumula los contadores de una corrida (seguro para uso concurrente).
type Stats struct {
	mu        sync.Mutex
	Params    Params    `json:"params"`
	State     string    `json:"state"` // idle | running | done | stopped | error
	StartedAt time.Time `json:"started_at"`
	EndedAt   time.Time `json:"ended_at,omitempty"`
	Sent      int       `json:"sent"`
	Errors    int       `json:"errors"`
	Foreign   int       `json:"foreign"`
	LastError string    `json:"last_error,omitempty"`
	Traces    []string  `json:"traces"`
	lat       []time.Duration
	series    []int // msgs enviados por segundo
}

// Snapshot es la vista serializable de Stats.
type Snapshot struct {
	Params     Params    `json:"params"`
	State      string    `json:"state"`
	StartedAt  time.Time `json:"started_at"`
	EndedAt    time.Time `json:"ended_at"`
	ElapsedSec float64   `json:"elapsed_sec"`
	Sent       int       `json:"sent"`
	Errors     int       `json:"errors"`
	Foreign    int       `json:"foreign"`
	LastError  string    `json:"last_error,omitempty"`
	RealRate   float64   `json:"real_rate"` // msg/s medidos en los últimos 5 s
	P50ms      float64   `json:"p50_ms"`
	P95ms      float64   `json:"p95_ms"`
	Traces     []string  `json:"traces"`
	Series     []int     `json:"series"`
}

func (s *Stats) snapshot() Snapshot {
	s.mu.Lock()
	defer s.mu.Unlock()
	end := s.EndedAt
	if s.State == "running" || end.IsZero() {
		end = time.Now()
	}
	snap := Snapshot{
		Params: s.Params, State: s.State, StartedAt: s.StartedAt, EndedAt: s.EndedAt,
		Sent: s.Sent, Errors: s.Errors, Foreign: s.Foreign, LastError: s.LastError,
		Traces: append([]string(nil), s.Traces...), Series: append([]int(nil), s.series...),
	}
	if !s.StartedAt.IsZero() {
		snap.ElapsedSec = end.Sub(s.StartedAt).Seconds()
	}
	if n := len(s.series); n > 0 {
		w := min(5, n)
		sum := 0
		for _, v := range s.series[n-w:] {
			sum += v
		}
		snap.RealRate = float64(sum) / float64(w)
	}
	if len(s.lat) > 0 {
		l := append([]time.Duration(nil), s.lat...)
		sort.Slice(l, func(i, j int) bool { return l[i] < l[j] })
		snap.P50ms = float64(l[len(l)/2]) / float64(time.Millisecond)
		snap.P95ms = float64(l[len(l)*95/100]) / float64(time.Millisecond)
	}
	return snap
}

const maxLat = 2000

func (s *Stats) record(lat time.Duration, err error, foreign bool, traceID string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if err != nil {
		s.Errors++
		s.LastError = err.Error()
		return
	}
	s.Sent++
	if foreign {
		s.Foreign++
	}
	if len(s.lat) >= maxLat {
		s.lat = s.lat[1:]
	}
	s.lat = append(s.lat, lat)
	if len(s.Traces) < 5 {
		s.Traces = append(s.Traces, traceID)
	}
}

// sampler agrega una muestra por segundo a series hasta que ctx termine.
func (s *Stats) sampler(ctx context.Context) {
	t := time.NewTicker(time.Second)
	defer t.Stop()
	last := 0
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			s.mu.Lock()
			s.series = append(s.series, s.Sent-last)
			last = s.Sent
			if len(s.series) > 900 {
				s.series = s.series[1:]
			}
			s.mu.Unlock()
		}
	}
}

// run ejecuta el escenario publicando en la Cola de Declaración. Bloquea hasta terminar.
func run(ctx context.Context, cl *kgo.Client, p Params, st *Stats) {
	sctx, stopSampler := context.WithCancel(ctx)
	defer stopSampler()
	st.mu.Lock()
	st.Params, st.State, st.StartedAt = p, "running", time.Now()
	st.mu.Unlock()
	go st.sampler(sctx)

	region := platform.Region()
	start := time.Now()
	for i := 0; ctx.Err() == nil && !p.done(i, time.Since(start)); i++ {
		kind := domain.Surplus
		if i%2 == 1 {
			kind = domain.Deficit
		}
		home := region
		foreign := rand.Float64() < p.Foreign
		if foreign {
			home = domain.OtherRegion(region)
		}
		d := domain.Declaration{
			MeterID:    fmt.Sprintf("AMI-%s-%04d", home, rand.IntN(p.Meters)+1),
			Kind:       kind,
			KWh:        domain.Round(0.5+rand.Float64()*4.5, 3),
			MeasuredAt: time.Now().UTC(),
		}
		ev, _ := platform.NewEvent(domain.TypeDeclaration, "//"+region+"/ami-injector", d)
		pctx, span := platform.Tracer().Start(ctx, "inject declaration", trace.WithSpanKind(trace.SpanKindInternal))
		t0 := time.Now()
		err := platform.Publish(pctx, cl, domain.TopicIngress, d.MeterID, ev)
		lat := time.Since(t0)
		tid := span.SpanContext().TraceID().String()
		span.End()
		st.record(lat, err, foreign, tid)

		select {
		case <-ctx.Done():
		case <-time.After(p.delayAfter(i, time.Since(start))):
		}
	}

	st.mu.Lock()
	st.EndedAt = time.Now()
	switch {
	case ctx.Err() != nil:
		st.State = "stopped"
	case st.Sent == 0 && st.Errors > 0:
		st.State = "error"
	default:
		st.State = "done"
	}
	st.mu.Unlock()
}
