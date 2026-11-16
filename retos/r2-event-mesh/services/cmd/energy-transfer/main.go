// energy-transfer: recibe EnergyMatched y simula en logs los comandos hacia los
// switches físicos (sin hardware real).
package main

import (
	"context"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"

	"github.com/csbenitez/voltera-eda/internal/domain"
	"github.com/csbenitez/voltera-eda/internal/platform"
)

func main() {
	platform.Run("energy-transfer", func(ctx context.Context) error {
		cl, err := platform.NewKafka(platform.ConsumerOpts("energy-transfer", domain.TopicMatched)...)
		if err != nil {
			return err
		}
		defer cl.Close()
		go func() { _ = platform.Serve(ctx, nil) }()
		delay := platform.EnvDuration("SWITCH_LATENCY", 30*time.Millisecond)

		return platform.Consume(ctx, cl, 3, func(ctx context.Context, ev platform.Event, _ *kgo.Record) error {
			var m domain.EnergyMatched
			if err := ev.Decode(&m); err != nil {
				return nil
			}
			_, span := platform.Tracer().Start(ctx, "switch command")
			time.Sleep(delay) // latencia simulada del SCADA
			span.End()
			platform.Log(ctx).Info("SWITCH_CMD",
				"match", m.MatchID, "action", "CLOSE_FEEDER",
				"from_meter", m.SellerMeter, "to_meter", m.BuyerMeter, "kwh", m.KWh,
				"origin", m.OriginRegion, "processed_by", m.ProcessedBy)
			return nil
		})
	})
}
