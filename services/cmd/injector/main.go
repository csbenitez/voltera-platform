// injector: inyección sintética de declaraciones (medidores AMI) en la Cola de
// Declaración. Cada mensaje inicia una traza raíz; se imprimen los trace_id para
// buscarlos en Application Insights.
//
// Dos modos:
//
//	injector -n 200 -rate 50 -meters 40        # CLI de un solo disparo (Job)
//	injector -serve                            # dashboard (index.html) con escenarios de carga
package main

import (
	"context"
	"flag"
	"fmt"
	"time"

	"github.com/csbenitez/voltera-eda/internal/platform"
)

func main() {
	n := flag.Int("n", platform.EnvInt("INJECT_N", 100), "número de declaraciones")
	rate := flag.Int("rate", platform.EnvInt("INJECT_RATE", 50), "mensajes por segundo")
	meters := flag.Int("meters", platform.EnvInt("INJECT_METERS", 40), "medidores distintos")
	foreign := flag.Float64("foreign", 0, "fracción (0..1) de medidores que pertenecen a la otra región (caso de uso Knative)")
	serve := flag.Bool("serve", platform.Env("INJECT_SERVE", "") == "1", "levanta el dashboard HTTP en vez de inyectar una vez")
	flag.Parse()

	platform.Run("injector", func(ctx context.Context) error {
		cl, err := platform.NewKafka()
		if err != nil {
			return err
		}
		defer cl.Close()

		if *serve {
			return serveDashboard(ctx, cl)
		}

		p := Params{Scenario: ScConstant, N: *n, Rate: *rate, Meters: *meters, Foreign: *foreign}
		if err := p.normalize(); err != nil {
			return err
		}
		st := &Stats{}
		run(ctx, cl, p, st)
		s := st.snapshot()
		fmt.Printf("\nInyectadas %d declaraciones en %s (region=%s, errores=%d)\n",
			s.Sent, time.Duration(s.ElapsedSec*float64(time.Second)).Truncate(time.Millisecond), platform.Region(), s.Errors)
		fmt.Println("Trace IDs de ejemplo (App Insights -> Transaction search / operation_Id):")
		for _, t := range s.Traces {
			fmt.Println("  ", t)
		}
		if s.State == "error" {
			return fmt.Errorf("ninguna declaración publicada: %s", s.LastError)
		}
		return nil
	})
}
