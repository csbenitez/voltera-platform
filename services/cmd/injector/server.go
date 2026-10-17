package main

import (
	"context"
	_ "embed"
	"encoding/json"
	"net/http"
	"sync"

	"github.com/twmb/franz-go/pkg/kgo"

	"github.com/csbenitez/voltera-eda/internal/platform"
)

//go:embed web/index.html
var indexHTML []byte

const maxHistory = 10

// controller serializa las corridas: solo una activa a la vez.
type controller struct {
	cl  *kgo.Client
	ctx context.Context

	mu      sync.Mutex
	current *Stats
	cancel  context.CancelFunc
	history []Snapshot
}

func serveDashboard(ctx context.Context, cl *kgo.Client) error {
	c := &controller{cl: cl, ctx: ctx, current: &Stats{State: "idle"}}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /{$}", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		_, _ = w.Write(indexHTML)
	})
	mux.HandleFunc("GET /api/scenarios", func(w http.ResponseWriter, _ *http.Request) {
		platform.WriteJSON(w, http.StatusOK, map[string]any{"region": platform.Region(), "presets": Presets()})
	})
	mux.HandleFunc("POST /api/run", c.start)
	mux.HandleFunc("POST /api/stop", c.stop)
	mux.HandleFunc("GET /api/status", c.status)
	return platform.Serve(ctx, mux)
}

func (c *controller) start(w http.ResponseWriter, r *http.Request) {
	var p Params
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<16)).Decode(&p); err != nil {
		http.Error(w, "JSON inválido: "+err.Error(), http.StatusBadRequest)
		return
	}
	if err := p.normalize(); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.cancel != nil {
		http.Error(w, "ya hay una corrida activa; deténla primero", http.StatusConflict)
		return
	}
	st := &Stats{State: "running", Params: p}
	rctx, cancel := context.WithCancel(c.ctx)
	c.current, c.cancel = st, cancel
	go func() {
		run(rctx, c.cl, p, st)
		c.mu.Lock()
		c.history = append([]Snapshot{st.snapshot()}, c.history...)
		if len(c.history) > maxHistory {
			c.history = c.history[:maxHistory]
		}
		c.cancel = nil
		c.mu.Unlock()
		cancel()
	}()
	platform.WriteJSON(w, http.StatusAccepted, map[string]any{"started": true, "params": p})
}

func (c *controller) stop(w http.ResponseWriter, _ *http.Request) {
	c.mu.Lock()
	cancel := c.cancel
	c.mu.Unlock()
	if cancel != nil {
		cancel()
	}
	platform.WriteJSON(w, http.StatusOK, map[string]any{"stopping": cancel != nil})
}

func (c *controller) status(w http.ResponseWriter, _ *http.Request) {
	c.mu.Lock()
	cur, hist := c.current, append([]Snapshot(nil), c.history...)
	c.mu.Unlock()
	platform.WriteJSON(w, http.StatusOK, map[string]any{
		"region": platform.Region(), "current": cur.snapshot(), "history": hist,
	})
}
