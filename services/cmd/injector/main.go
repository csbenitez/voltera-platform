// injector: inyección sintética de N declaraciones (medidores AMI) en la Cola de
// Declaración. Cada mensaje inicia una traza raíz; se imprimen los trace_id para
// buscarlos en Application Insights.
//
//	injector -n 200 -rate 50 -meters 40
package main

import (
	"context"
	"flag"
	"fmt"
	"math/rand/v2"
	"time"

	"go.opentelemetry.io/otel/trace"

	"github.com/csbenitez/voltera-eda/internal/domain"
	"github.com/csbenitez/voltera-eda/internal/platform"
)

func main() {
	n := flag.Int("n", platform.EnvInt("INJECT_N", 100), "número de declaraciones")
	rate := flag.Int("rate", platform.EnvInt("INJECT_RATE", 50), "mensajes por segundo")
	meters := flag.Int("meters", platform.EnvInt("INJECT_METERS", 40), "medidores distintos")
	flag.Parse()

	platform.Run("injector", func(ctx context.Context) error {
		cl, err := platform.NewKafka()
		if err != nil {
			return err
		}
		defer cl.Close()

		region := platform.Region()
		interval := time.Second / time.Duration(max(1, *rate))
		start := time.Now()
		var traces []string
		for i := 0; i < *n && ctx.Err() == nil; i++ {
			kind := domain.Surplus
			if i%2 == 1 {
				kind = domain.Deficit
			}
			d := domain.Declaration{
				MeterID:    fmt.Sprintf("AMI-%s-%04d", region, rand.IntN(*meters)+1),
				Kind:       kind,
				KWh:        domain.Round(0.5+rand.Float64()*4.5, 3),
				MeasuredAt: time.Now().UTC(),
			}
			ev, _ := platform.NewEvent(domain.TypeDeclaration, "//"+region+"/ami-injector", d)
			sctx, span := platform.Tracer().Start(ctx, "inject declaration", trace.WithSpanKind(trace.SpanKindInternal))
			if err := platform.Publish(sctx, cl, domain.TopicIngress, d.MeterID, ev); err != nil {
				span.End()
				return err
			}
			if len(traces) < 5 {
				traces = append(traces, span.SpanContext().TraceID().String())
			}
			span.End()
			time.Sleep(interval)
		}
		fmt.Printf("\nInyectadas %d declaraciones en %s (region=%s)\n", *n, time.Since(start).Truncate(time.Millisecond), region)
		fmt.Println("Trace IDs de ejemplo (App Insights -> Transaction search / operation_Id):")
		for _, t := range traces {
			fmt.Println("  ", t)
		}
		return nil
	})
}
