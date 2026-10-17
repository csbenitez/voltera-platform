// price-feed-simulator: emite periódicamente el evento "Precio kWh" con una
// caminata aleatoria acotada entre PRICE_MIN y PRICE_MAX (COP/kWh).
package main

import (
	"context"
	"math/rand/v2"
	"time"

	"github.com/csbenitez/voltera-eda/internal/domain"
	"github.com/csbenitez/voltera-eda/internal/platform"
)

func main() {
	platform.Run("price-feed-simulator", func(ctx context.Context) error {
		cl, err := platform.NewKafka()
		if err != nil {
			return err
		}
		defer cl.Close()
		go func() { _ = platform.Serve(ctx, nil) }()

		minP := platform.EnvFloat("PRICE_MIN", 650)
		maxP := platform.EnvFloat("PRICE_MAX", 950)
		price := (minP + maxP) / 2
		tick := time.NewTicker(platform.EnvDuration("PRICE_INTERVAL", 10*time.Second))
		defer tick.Stop()

		for {
			price += (rand.Float64() - 0.5) * (maxP - minP) * 0.1
			price = max(minP, min(maxP, price))
			p := domain.PriceKWh{
				PricePerKWh: domain.Round(price, 2), Currency: "COP",
				ValidFrom: time.Now().UTC(), Region: platform.Region(),
			}
			ev, _ := platform.NewEvent(domain.TypePrice, "//"+platform.Region()+"/price-feed-simulator", p)
			pctx, span := platform.Tracer().Start(ctx, "emit price")
			if err := platform.Publish(pctx, cl, domain.TopicPrices, "price", ev); err != nil {
				platform.Log(pctx).Warn("no se pudo publicar precio", "err", err)
			} else {
				platform.Log(pctx).Info("precio publicado", "price_per_kwh", p.PricePerKWh)
			}
			span.End()

			select {
			case <-ctx.Done():
				return nil
			case <-tick.C:
			}
		}
	})
}
