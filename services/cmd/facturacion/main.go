// facturacion (mock sistema externo, solo AKS): consumer dummy que valida y
// registra los cierres de calce entregados por el Event Mesh.
package main

import (
	"context"
	"net/http"
	"sync"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"

	"github.com/csbenitez/voltera-eda/internal/domain"
	"github.com/csbenitez/voltera-eda/internal/platform"
)

type invoice struct {
	domain.EnergyMatched
	InvoiceID  string    `json:"invoice_id"`
	TraceID    string    `json:"trace_id"`
	RecordedAt time.Time `json:"recorded_at"`
	LatencyMS  int64     `json:"e2e_latency_ms"` // desde matched_at hasta registro
}

type ledger struct {
	mu      sync.Mutex
	seen    map[string]bool
	recent  []invoice
	total   int64
	invalid int64
	dups    int64
	kwh     float64
	amount  float64
	byRoute map[string]int64 // origin->processed_by
}

func main() {
	platform.Run("facturacion", func(ctx context.Context) error {
		l := &ledger{seen: map[string]bool{}, byRoute: map[string]int64{}}
		cl, err := platform.NewKafka(platform.ConsumerOpts("facturacion", domain.TopicBilling)...)
		if err != nil {
			return err
		}
		defer cl.Close()

		mux := http.NewServeMux()
		mux.HandleFunc("GET /stats", l.stats)
		mux.HandleFunc("GET /invoices", l.invoices)
		go func() { _ = platform.Serve(ctx, mux) }()

		return platform.Consume(ctx, cl, 3, func(ctx context.Context, ev platform.Event, _ *kgo.Record) error {
			var m domain.EnergyMatched
			if err := ev.Decode(&m); err != nil {
				l.mu.Lock()
				l.invalid++
				l.mu.Unlock()
				return nil
			}
			l.record(ctx, m)
			return nil
		})
	})
}

func (l *ledger) record(ctx context.Context, m domain.EnergyMatched) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if err := m.Validate(); err != nil {
		l.invalid++
		platform.Log(ctx).Warn("cierre de calce rechazado", "match", m.MatchID, "err", err)
		return
	}
	if l.seen[m.MatchID] {
		l.dups++
		return
	}
	l.seen[m.MatchID] = true
	now := time.Now().UTC()
	inv := invoice{EnergyMatched: m, InvoiceID: "FAC-" + platform.NewID()[:12], RecordedAt: now,
		LatencyMS: now.Sub(m.MatchedAt).Milliseconds()}
	if sc := platform.TraceparentOf(ctx); len(sc) > 35 {
		inv.TraceID = sc[3:35]
	}
	l.total++
	l.kwh += m.KWh
	l.amount += m.Total
	l.byRoute[m.OriginRegion+"->"+m.ProcessedBy]++
	l.recent = append(l.recent, inv)
	if len(l.recent) > 100 {
		l.recent = l.recent[1:]
	}
	platform.Log(ctx).Info("FACTURA_REGISTRADA", "invoice", inv.InvoiceID, "match", m.MatchID,
		"kwh", m.KWh, "total", m.Total, "origin", m.OriginRegion, "processed_by", m.ProcessedBy,
		"e2e_latency_ms", inv.LatencyMS)
}

func (l *ledger) stats(w http.ResponseWriter, _ *http.Request) {
	l.mu.Lock()
	defer l.mu.Unlock()
	platform.WriteJSON(w, http.StatusOK, map[string]any{
		"invoices": l.total, "invalid": l.invalid, "duplicates": l.dups,
		"kwh": domain.Round(l.kwh, 3), "amount": domain.Round(l.amount, 2),
		"by_route": l.byRoute,
	})
}

func (l *ledger) invoices(w http.ResponseWriter, _ *http.Request) {
	l.mu.Lock()
	defer l.mu.Unlock()
	platform.WriteJSON(w, http.StatusOK, l.recent)
}
