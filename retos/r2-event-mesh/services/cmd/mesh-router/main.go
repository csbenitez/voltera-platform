// mesh-router (solo AKS): Event Mesh central del lab.
//
//   - Ingesta HTTP /mesh/events (región on-prem vía mesh-bridge), autenticada con X-Mesh-Token.
//   - Ingesta Kafka de EnergyMatched de la región nube (Redpanda local de AKS).
//   - Enruta EnergyMatched consolidado y deduplicado (Redis SETNX ce-id) al topic de Facturación.
//   - Failover: si el heartbeat del matching-engine de FAILOVER_REGION deja de llegar
//     por más de FAILOVER_AFTER, reenvía las posiciones/precios de esa región (incluido
//     un replay del buffer reciente) al matching-engine de la nube.
package main

import (
	"context"
	"crypto/subtle"
	"errors"
	"io"
	"net/http"
	"sync"
	"time"

	"github.com/redis/go-redis/v9"
	"github.com/twmb/franz-go/pkg/kgo"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/trace"

	"github.com/csbenitez/voltera-eda/internal/domain"
	"github.com/csbenitez/voltera-eda/internal/platform"
)

const (
	StateNormal   = "NORMAL"
	StateFailover = "FAILOVER_ACTIVE"
)

type buffered struct {
	ctx context.Context
	ev  platform.Event
	at  time.Time
}

type router struct {
	rdb    *redis.Client
	kafka  *kgo.Client
	token  string
	region string // región bajo vigilancia (onprem)

	failoverAfter time.Duration
	bufferWindow  time.Duration
	startedAt     time.Time

	mu             sync.Mutex
	state          string
	stateSince     time.Time
	lastHeartbeat  time.Time
	lastPositionAt time.Time
	lastPrice      *buffered
	buffer         []buffered
	transitions    []map[string]any
	counters       map[string]int64
}

func main() {
	platform.Run("mesh-router", func(ctx context.Context) error {
		token := platform.Env("MESH_TOKEN", "")
		if token == "" {
			return errors.New("MESH_TOKEN requerido")
		}
		rdb := redis.NewClient(&redis.Options{Addr: platform.Env("REDIS_ADDR", "redis:6379")})
		defer rdb.Close()
		producer, err := platform.NewKafka()
		if err != nil {
			return err
		}
		defer producer.Close()

		now := time.Now()
		r := &router{
			rdb: rdb, kafka: producer, token: token,
			region:        platform.Env("FAILOVER_REGION", "onprem"),
			failoverAfter: platform.EnvDuration("FAILOVER_AFTER", 15*time.Second),
			bufferWindow:  platform.EnvDuration("REPLAY_WINDOW", 60*time.Second),
			startedAt:     now, state: StateNormal, stateSince: now,
			counters: map[string]int64{},
		}

		consumer, err := platform.NewKafka(platform.ConsumerOpts("mesh-router", domain.TopicMatched)...)
		if err != nil {
			return err
		}
		defer consumer.Close()

		mux := http.NewServeMux()
		mux.HandleFunc("POST /mesh/events", r.ingestHTTP)
		mux.HandleFunc("GET /status", r.status)
		go func() { _ = platform.Serve(ctx, mux) }()
		go r.watchdog(ctx)

		// EnergyMatched de la región nube (incluye calces hechos en failover).
		return platform.Consume(ctx, consumer, 5, func(ctx context.Context, ev platform.Event, _ *kgo.Record) error {
			return r.routeToBilling(ctx, ev, "kafka-cloud")
		})
	})
}

func (r *router) inc(k string) {
	r.mu.Lock()
	r.counters[k]++
	r.mu.Unlock()
}

// ingestHTTP recibe CloudEvents binarios desde el mesh-bridge on-prem.
func (r *router) ingestHTTP(w http.ResponseWriter, req *http.Request) {
	if subtle.ConstantTimeCompare([]byte(req.Header.Get("X-Mesh-Token")), []byte(r.token)) != 1 {
		r.inc("rejected_auth")
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	body, err := io.ReadAll(io.LimitReader(req.Body, 1<<20))
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	ctx, ev, err := platform.FromHTTP(req, body)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	var span trace.Span = trace.SpanFromContext(ctx)
	if !platform.Untraced(ev.Type) {
		ctx, span = platform.Tracer().Start(ctx, "mesh ingest "+ev.Type, trace.WithSpanKind(trace.SpanKindServer),
			trace.WithAttributes(
				attribute.String("http.request.method", "POST"),
				attribute.String("http.route", "/mesh/events"),
				attribute.String("cloudevents.event_id", ev.ID),
				attribute.String("voltera.origin_region", ev.Region),
			))
		defer span.End()
	}

	if err := r.dispatch(ctx, ev); err != nil {
		span.SetStatus(codes.Error, err.Error())
		http.Error(w, err.Error(), http.StatusServiceUnavailable) // el bridge reintenta
		return
	}
	w.WriteHeader(http.StatusAccepted)
}

func (r *router) dispatch(ctx context.Context, ev platform.Event) error {
	r.inc("in_" + ev.Type)
	switch ev.Type {
	case domain.TypeMatched:
		return r.routeToBilling(ctx, ev, "http-"+ev.Region)
	case domain.TypeHeartbeat:
		var hb domain.Heartbeat
		if err := ev.Decode(&hb); err != nil {
			return nil
		}
		r.onHeartbeat(ctx, hb)
		return nil
	case domain.TypePosition, domain.TypePrice:
		return r.onMarketEvent(ctx, ev)
	}
	return nil
}

// routeToBilling: conector de salida del mesh hacia Facturación, con dedup global.
func (r *router) routeToBilling(ctx context.Context, ev platform.Event, via string) error {
	ok, err := r.rdb.SetNX(ctx, "mesh:seen:"+ev.ID, via, 24*time.Hour).Result()
	if err != nil {
		return err
	}
	if !ok {
		r.inc("billing_duplicates")
		platform.Log(ctx).Info("EnergyMatched duplicado descartado", "id", ev.ID, "via", via)
		return nil
	}
	if err := platform.Publish(ctx, r.kafka, domain.TopicBilling, ev.ID, ev); err != nil {
		r.rdb.Del(ctx, "mesh:seen:"+ev.ID)
		return err
	}
	r.inc("billing_routed_" + ev.Region)
	platform.Log(ctx).Info("EnergyMatched enrutado a facturación", "id", ev.ID, "origin", ev.Region, "via", via)
	return nil
}

func (r *router) onHeartbeat(ctx context.Context, hb domain.Heartbeat) {
	if hb.Region != r.region || time.Since(hb.At).Abs() > time.Minute {
		return // otra región o latido viejo (backlog del bridge)
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	r.lastHeartbeat = time.Now()
	if hb.LastPositionAt.After(r.lastPositionAt) {
		r.lastPositionAt = hb.LastPositionAt
	}
	if r.state == StateFailover {
		r.transition(ctx, StateNormal, "heartbeat de "+r.region+" recuperado")
	}
}

func (r *router) onMarketEvent(ctx context.Context, ev platform.Event) error {
	if ev.Region != r.region {
		return nil
	}
	r.mu.Lock()
	b := buffered{ctx: ctx, ev: ev, at: time.Now()}
	if ev.Type == domain.TypePrice {
		r.lastPrice = &b
	} else {
		r.buffer = append(r.buffer, b)
		r.trimBuffer()
	}
	active := r.state == StateFailover
	r.mu.Unlock()

	if !active {
		return nil
	}
	if err := platform.Publish(ctx, r.kafka, domain.TopicFailover, ev.ID, ev); err != nil {
		return err
	}
	r.inc("failover_forwarded")
	return nil
}

func (r *router) trimBuffer() {
	cut := time.Now().Add(-r.bufferWindow)
	i := 0
	for i < len(r.buffer) && r.buffer[i].at.Before(cut) {
		i++
	}
	r.buffer = r.buffer[i:]
}

// watchdog evalúa la salud de la región vigilada cada segundo.
func (r *router) watchdog(ctx context.Context) {
	t := time.NewTicker(time.Second)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
		r.mu.Lock()
		ref := r.lastHeartbeat
		if ref.IsZero() {
			ref = r.startedAt // nunca se ha visto la región: se da un periodo de gracia
		}
		if r.state == StateNormal && time.Since(ref) > r.failoverAfter {
			r.transition(ctx, StateFailover, "sin heartbeat de "+r.region+" por "+time.Since(ref).Truncate(time.Second).String())
			replay := r.replaySet()
			r.mu.Unlock()
			r.replay(ctx, replay)
			continue
		}
		r.mu.Unlock()
	}
}

// replaySet: precio vigente + posiciones posteriores a la última procesada por el engine caído.
func (r *router) replaySet() []buffered {
	var out []buffered
	if r.lastPrice != nil {
		out = append(out, *r.lastPrice)
	}
	for _, b := range r.buffer {
		if b.ev.Time.After(r.lastPositionAt) {
			out = append(out, b)
		}
	}
	return out
}

func (r *router) replay(ctx context.Context, items []buffered) {
	for _, b := range items {
		pctx := trace.ContextWithSpanContext(ctx, trace.SpanContextFromContext(b.ctx))
		pctx, span := platform.Tracer().Start(pctx, "failover replay")
		if err := platform.Publish(pctx, r.kafka, domain.TopicFailover, b.ev.ID, b.ev); err != nil {
			platform.Log(pctx).Warn("replay falló", "id", b.ev.ID, "err", err)
		} else {
			r.inc("failover_replayed")
		}
		span.End()
	}
	platform.Log(ctx).Warn("failover: replay enviado al matching-engine de la nube", "events", len(items))
}

// transition debe llamarse con r.mu tomado.
func (r *router) transition(ctx context.Context, to, reason string) {
	from := r.state
	r.state, r.stateSince = to, time.Now()
	r.transitions = append(r.transitions, map[string]any{"at": r.stateSince.UTC(), "from": from, "to": to, "reason": reason})
	if len(r.transitions) > 20 {
		r.transitions = r.transitions[1:]
	}
	platform.Log(ctx).Warn("mesh state change", "from", from, "to", to, "reason", reason)
}

func (r *router) status(w http.ResponseWriter, _ *http.Request) {
	r.mu.Lock()
	defer r.mu.Unlock()
	counters := make(map[string]int64, len(r.counters))
	for k, v := range r.counters {
		counters[k] = v
	}
	var hbAge any
	if !r.lastHeartbeat.IsZero() {
		hbAge = time.Since(r.lastHeartbeat).Truncate(time.Millisecond).String()
	}
	platform.WriteJSON(w, http.StatusOK, map[string]any{
		"state":            r.state,
		"state_since":      r.stateSince.UTC(),
		"watched_region":   r.region,
		"failover_after":   r.failoverAfter.String(),
		"last_heartbeat":   r.lastHeartbeat.UTC(),
		"heartbeat_age":    hbAge,
		"last_position_at": r.lastPositionAt.UTC(),
		"buffered":         len(r.buffer),
		"counters":         counters,
		"transitions":      r.transitions,
	})
}
