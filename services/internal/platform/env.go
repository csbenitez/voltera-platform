// Package platform agrupa lo transversal a todos los mocks del lab:
// configuración por variables de entorno, logging, trazas OTel,
// sobre CloudEvents y helpers de Kafka (Redpanda) y HTTP.
package platform

import (
	"context"
	"log/slog"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"go.opentelemetry.io/otel/trace"
)

func Env(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func EnvInt(key string, def int) int {
	if v, err := strconv.Atoi(os.Getenv(key)); err == nil {
		return v
	}
	return def
}

func EnvFloat(key string, def float64) float64 {
	if v, err := strconv.ParseFloat(os.Getenv(key), 64); err == nil {
		return v
	}
	return def
}

func EnvDuration(key string, def time.Duration) time.Duration {
	if v, err := time.ParseDuration(os.Getenv(key)); err == nil {
		return v
	}
	return def
}

// Region identifica la región simulada (onprem = k3d, cloud = AKS).
func Region() string { return Env("REGION", "onprem") }

// Log devuelve un logger con trace_id/span_id para correlacionar con App Insights.
func Log(ctx context.Context) *slog.Logger {
	sc := trace.SpanContextFromContext(ctx)
	if !sc.IsValid() {
		return slog.Default()
	}
	return slog.Default().With("trace_id", sc.TraceID().String(), "span_id", sc.SpanID().String())
}

// Run inicializa logging + tracing y ejecuta fn hasta SIGINT/SIGTERM.
func Run(service string, fn func(ctx context.Context) error) {
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, nil)).With("service", service, "region", Region()))

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	shutdown := InitTracing(ctx, service)
	defer func() {
		c, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = shutdown(c)
	}()

	slog.Info("starting")
	if err := fn(ctx); err != nil && ctx.Err() == nil {
		slog.Error("fatal", "err", err)
		os.Exit(1)
	}
	slog.Info("stopped")
}
