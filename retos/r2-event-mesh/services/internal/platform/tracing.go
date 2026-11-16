package platform

import (
	"context"
	"log/slog"
	"strings"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/trace"
)

var tracer trace.Tracer = otel.Tracer("voltera-eda")

// InitTracing configura el exportador OTLP (hacia el otel-collector del cluster,
// que a su vez exporta a Application Insights). Sin OTEL_EXPORTER_OTLP_ENDPOINT
// se generan y propagan trace ids igualmente, pero no se exportan.
func InitTracing(ctx context.Context, service string) func(context.Context) error {
	otel.SetTextMapPropagator(propagation.TraceContext{})

	opts := []sdktrace.TracerProviderOption{}
	if Env("OTEL_EXPORTER_OTLP_ENDPOINT", "") != "" {
		if exp, err := otlptracegrpc.New(ctx); err != nil { // lee OTEL_EXPORTER_OTLP_ENDPOINT / _INSECURE
			slog.Warn("otlp exporter disabled", "err", err)
		} else {
			opts = append(opts, sdktrace.WithBatcher(exp))
		}
	}
	res := resource.NewSchemaless(
		attribute.String("service.name", service),
		attribute.String("service.namespace", "voltera"),
		attribute.String("service.instance.id", Env("HOSTNAME", service)),
		attribute.String("cloud.region", Region()),
		attribute.String("deployment.environment", "lab"),
	)
	tp := sdktrace.NewTracerProvider(append(opts, sdktrace.WithResource(res))...)
	otel.SetTracerProvider(tp)
	tracer = tp.Tracer("voltera-eda/" + service)
	return tp.Shutdown
}

func Tracer() trace.Tracer { return tracer }

// TraceparentOf serializa el contexto de traza actual (para outbox y span links).
func TraceparentOf(ctx context.Context) string {
	c := propagation.MapCarrier{}
	otel.GetTextMapPropagator().Inject(ctx, c)
	return c["traceparent"]
}

// ContextFromTraceparent reconstruye un contexto remoto a partir de un traceparent guardado.
func ContextFromTraceparent(ctx context.Context, tp string) context.Context {
	if tp == "" {
		return ctx
	}
	return otel.GetTextMapPropagator().Extract(ctx, propagation.MapCarrier{"traceparent": tp})
}

// LinkFromTraceparent crea un span link (ej. el match enlaza la posición contraparte).
func LinkFromTraceparent(tp string) (trace.Link, bool) {
	sc := trace.SpanContextFromContext(ContextFromTraceparent(context.Background(), tp))
	if !sc.IsValid() {
		return trace.Link{}, false
	}
	return trace.Link{SpanContext: sc}, true
}

// Untraced indica eventos de control (heartbeats) que no generan spans para no
// inundar App Insights con trazas raíz cada pocos segundos.
func Untraced(eventType string) bool { return strings.HasSuffix(eventType, ".heartbeat.v1") }
