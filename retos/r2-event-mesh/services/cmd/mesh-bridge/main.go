// mesh-bridge (solo región on-prem): conector de la región local hacia el
// Event Mesh central en AKS. Consume EnergyMatched, PositionDeclared, Precio kWh
// y heartbeats del Redpanda local y los envía como CloudEvents HTTP (binario)
// al mesh-router a través del Istio ingress (TLS + X-Mesh-Token).
// El commit de offsets ocurre solo tras un 2xx: si la nube no responde, el lag
// se acumula en Redpanda y no se pierde ningún evento.
package main

import (
	"bytes"
	"context"
	"crypto/tls"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/trace"

	"github.com/csbenitez/voltera-eda/internal/domain"
	"github.com/csbenitez/voltera-eda/internal/platform"
)

func main() {
	platform.Run("mesh-bridge", func(ctx context.Context) error {
		meshURL := platform.Env("MESH_URL", "https://voltera-dev-mesh.eastus2.cloudapp.azure.com/mesh/events")
		token := platform.Env("MESH_TOKEN", "")
		if token == "" {
			return fmt.Errorf("MESH_TOKEN requerido")
		}
		tr := http.DefaultTransport.(*http.Transport).Clone()
		if platform.Env("MESH_INSECURE_SKIP_VERIFY", "false") == "true" {
			tr.TLSClientConfig = &tls.Config{InsecureSkipVerify: true} //nolint:gosec // solo lab con certificados self-signed
		}
		client := &http.Client{Timeout: 10 * time.Second, Transport: tr}

		cl, err := platform.NewKafka(platform.ConsumerOpts("mesh-bridge",
			domain.TopicMatched, domain.TopicPositions, domain.TopicPrices, domain.TopicHeartbeat)...)
		if err != nil {
			return err
		}
		defer cl.Close()
		go func() { _ = platform.Serve(ctx, nil) }()

		return platform.ConsumeParallel(ctx, cl, platform.EnvInt("BRIDGE_WORKERS", 8), -1,
			func(ctx context.Context, ev platform.Event, _ *kgo.Record) error {
				return forward(ctx, client, meshURL, token, ev)
			})
	})
}

func forward(ctx context.Context, client *http.Client, url, token string, ev platform.Event) error {
	var span trace.Span = trace.SpanFromContext(ctx) // no-op para heartbeats
	if !platform.Untraced(ev.Type) {
		ctx, span = platform.Tracer().Start(ctx, "POST mesh-router", trace.WithSpanKind(trace.SpanKindClient),
			trace.WithAttributes(
				attribute.String("http.request.method", "POST"),
				attribute.String("url.full", url),
				attribute.String("cloudevents.event_type", ev.Type),
				attribute.String("messaging.message.id", ev.ID),
			))
		defer span.End()
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(ev.Data))
	if err != nil {
		return err
	}
	ev.ToHTTP(ctx, req)
	req.Header.Set("X-Mesh-Token", token)
	resp, err := client.Do(req)
	if err != nil {
		span.SetStatus(codes.Error, err.Error())
		return err
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, resp.Body)
	span.SetAttributes(attribute.Int("http.response.status_code", resp.StatusCode))
	if resp.StatusCode >= 300 {
		span.SetStatus(codes.Error, resp.Status)
		return fmt.Errorf("mesh respondió %s", resp.Status)
	}
	return nil
}
