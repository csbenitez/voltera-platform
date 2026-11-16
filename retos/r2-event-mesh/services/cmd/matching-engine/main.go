// matching-engine (incluye market-projector): proyecta PositionDeclared y
// Precio kWh en una vista de lectura Redis con TTL (CQRS) y ejecuta un calce
// First-Come-First-Served SURPLUS<->DEFICIT por región de origen. Publica
// EnergyMatched y un heartbeat que el Event Mesh usa para decidir el failover.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sync/atomic"
	"time"

	"github.com/redis/go-redis/v9"
	"github.com/twmb/franz-go/pkg/kgo"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/trace"

	"github.com/csbenitez/voltera-eda/internal/domain"
	"github.com/csbenitez/voltera-eda/internal/platform"
)

type bookEntry struct {
	domain.PositionDeclared
	Origin      string  `json:"origin"`
	Remaining   float64 `json:"remaining"`
	Traceparent string  `json:"traceparent"`
}

type engine struct {
	rdb            *redis.Client
	kafka          *kgo.Client
	region         string
	posTTL         time.Duration
	skipBefore     time.Time
	lastPositionAt atomic.Int64 // unix nanos
	matches        atomic.Int64
}

func main() {
	platform.Run("matching-engine", func(ctx context.Context) error {
		rdb := redis.NewClient(&redis.Options{Addr: platform.Env("REDIS_ADDR", "redis:6379")})
		defer rdb.Close()
		producer, err := platform.NewKafka()
		if err != nil {
			return err
		}
		defer producer.Close()

		e := &engine{rdb: rdb, kafka: producer, region: platform.Region(),
			posTTL: platform.EnvDuration("POSITION_TTL", 10*time.Minute)}
		e.detectFailoverGap(ctx, platform.EnvDuration("FAILOVER_AFTER", 15*time.Second))
		e.heartbeat(ctx) // anunciarse al mesh antes de consumir backlog
		go e.heartbeatLoop(ctx, platform.EnvDuration("HEARTBEAT_INTERVAL", 5*time.Second))
		go func() { _ = platform.Serve(ctx, nil) }()

		consumer, err := platform.NewKafka(platform.ConsumerOpts("matching-engine",
			domain.TopicPositions, domain.TopicPrices, domain.TopicFailover)...)
		if err != nil {
			return err
		}
		defer consumer.Close()
		return platform.Consume(ctx, consumer, 3, e.handle)
	})
}

// detectFailoverGap: si el engine estuvo caído más que el umbral de failover,
// el mesh ya reenvió esas posiciones a la nube; se omite el backlog anterior al
// arranque para no calzar dos veces.
func (e *engine) detectFailoverGap(ctx context.Context, threshold time.Duration) {
	last, err := e.rdb.Get(ctx, "engine:last_alive").Int64()
	if err != nil {
		return
	}
	if gap := time.Since(time.Unix(0, last)); gap > threshold {
		e.skipBefore = time.Now().UTC()
		platform.Log(ctx).Warn("engine estuvo caído más que el umbral de failover; se omite backlog previo",
			"gap", gap.String(), "skip_before", e.skipBefore)
	}
}

func (e *engine) heartbeatLoop(ctx context.Context, every time.Duration) {
	t := time.NewTicker(every)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			e.heartbeat(ctx)
		}
	}
}

func (e *engine) heartbeat(ctx context.Context) {
	now := time.Now().UTC()
	hb := domain.Heartbeat{Service: "matching-engine", Region: e.region, At: now,
		LastPositionAt: time.Unix(0, e.lastPositionAt.Load()).UTC()}
	ev, _ := platform.NewEvent(domain.TypeHeartbeat, "//"+e.region+"/matching-engine", hb)
	// Heartbeats sin traza (contexto vacío) para no inundar App Insights.
	if err := e.kafka.ProduceSync(ctx, ev.ToRecord(context.Background(), domain.TopicHeartbeat, e.region)).FirstErr(); err != nil {
		platform.Log(ctx).Warn("heartbeat no publicado", "err", err)
		return
	}
	e.rdb.Set(ctx, "engine:last_alive", now.UnixNano(), 0)
}

func (e *engine) handle(ctx context.Context, ev platform.Event, _ *kgo.Record) error {
	switch ev.Type {
	case domain.TypePrice:
		return e.onPrice(ctx, ev)
	case domain.TypePosition:
		return e.onPosition(ctx, ev)
	default:
		return nil
	}
}

func (e *engine) onPrice(ctx context.Context, ev platform.Event) error {
	var p domain.PriceKWh
	if err := ev.Decode(&p); err != nil {
		return nil
	}
	return e.rdb.Set(ctx, "price:"+originOf(ev, p.Region), []byte(ev.Data), e.posTTL).Err()
}

func originOf(ev platform.Event, fallback string) string {
	if ev.Region != "" {
		return ev.Region
	}
	return fallback
}

func (e *engine) price(ctx context.Context, origin string) (domain.PriceKWh, error) {
	var p domain.PriceKWh
	raw, err := e.rdb.Get(ctx, "price:"+origin).Bytes()
	if errors.Is(err, redis.Nil) && origin != e.region {
		raw, err = e.rdb.Get(ctx, "price:"+e.region).Bytes()
	}
	if err != nil {
		return p, err
	}
	return p, json.Unmarshal(raw, &p)
}

func (e *engine) onPosition(ctx context.Context, ev platform.Event) error {
	var pos domain.PositionDeclared
	if err := ev.Decode(&pos); err != nil {
		return nil
	}
	if !e.skipBefore.IsZero() && ev.Time.Before(e.skipBefore) {
		platform.Log(ctx).Info("posición omitida (procesada por la nube durante failover)", "position", pos.PositionID)
		return nil
	}
	if ok, err := e.rdb.SetNX(ctx, "seen:"+ev.ID, 1, time.Hour).Result(); err != nil {
		return err
	} else if !ok {
		platform.Log(ctx).Info("posición duplicada ignorada", "position", pos.PositionID)
		return nil
	}
	defer e.lastPositionAt.Store(ev.Time.UnixNano())

	origin := originOf(ev, pos.Region)
	in := bookEntry{PositionDeclared: pos, Origin: origin, Remaining: pos.KWh, Traceparent: platform.TraceparentOf(ctx)}
	opposite := domain.Deficit
	if pos.Kind == domain.Deficit {
		opposite = domain.Surplus
	}

	if price, err := e.price(ctx, origin); err == nil {
		for in.Remaining > 1e-6 {
			cp, ok, err := e.popCounterparty(ctx, origin, opposite)
			if err != nil {
				return err
			}
			if !ok {
				break
			}
			qty := min(in.Remaining, cp.Remaining)
			if err := e.emitMatch(ctx, in, cp, qty, price); err != nil {
				// devolver la contraparte al frente del libro y reintentar el evento
				_ = e.pushFront(ctx, cp)
				return err
			}
			in.Remaining -= qty
			cp.Remaining -= qty
			if cp.Remaining > 1e-6 {
				if err := e.pushFront(ctx, cp); err != nil {
					return err
				}
			} else {
				e.rdb.Del(ctx, "pos:"+cp.PositionID)
			}
		}
	} else {
		platform.Log(ctx).Info("sin precio vigente; posición queda en libro", "origin", origin)
	}

	if in.Remaining > 1e-6 {
		raw, _ := json.Marshal(in)
		pipe := e.rdb.TxPipeline()
		pipe.Set(ctx, "pos:"+in.PositionID, raw, e.posTTL)
		pipe.RPush(ctx, bookKey(origin, in.Kind), in.PositionID)
		pipe.Expire(ctx, bookKey(origin, in.Kind), e.posTTL)
		_, err := pipe.Exec(ctx)
		return err
	}
	return nil
}

func bookKey(origin, kind string) string { return "book:" + origin + ":" + kind }

// popCounterparty saca la contraparte más antigua todavía vigente (TTL).
func (e *engine) popCounterparty(ctx context.Context, origin, kind string) (bookEntry, bool, error) {
	for {
		id, err := e.rdb.LPop(ctx, bookKey(origin, kind)).Result()
		if errors.Is(err, redis.Nil) {
			return bookEntry{}, false, nil
		}
		if err != nil {
			return bookEntry{}, false, err
		}
		raw, err := e.rdb.Get(ctx, "pos:"+id).Bytes()
		if errors.Is(err, redis.Nil) {
			continue // expirada por TTL
		}
		if err != nil {
			return bookEntry{}, false, err
		}
		var b bookEntry
		if err := json.Unmarshal(raw, &b); err != nil {
			continue
		}
		return b, true, nil
	}
}

func (e *engine) pushFront(ctx context.Context, b bookEntry) error {
	raw, _ := json.Marshal(b)
	pipe := e.rdb.TxPipeline()
	pipe.Set(ctx, "pos:"+b.PositionID, raw, e.posTTL)
	pipe.LPush(ctx, bookKey(b.Origin, b.Kind), b.PositionID)
	_, err := pipe.Exec(ctx)
	return err
}

func (e *engine) emitMatch(ctx context.Context, in, cp bookEntry, qty float64, price domain.PriceKWh) error {
	seller, buyer := in, cp
	if in.Kind == domain.Deficit {
		seller, buyer = cp, in
	}
	opts := []trace.SpanStartOption{trace.WithAttributes(
		attribute.String("voltera.seller_position", seller.PositionID),
		attribute.String("voltera.buyer_position", buyer.PositionID),
		attribute.String("voltera.origin_region", in.Origin),
		attribute.String("voltera.processed_by", e.region),
	)}
	if l, ok := platform.LinkFromTraceparent(cp.Traceparent); ok {
		opts = append(opts, trace.WithLinks(l))
	}
	ctx, span := platform.Tracer().Start(ctx, "match FCFS", opts...)
	defer span.End()

	qty = domain.Round(qty, 4)
	m := domain.EnergyMatched{
		MatchID: platform.NewID(), SellerPosition: seller.PositionID, BuyerPosition: buyer.PositionID,
		SellerMeter: seller.MeterID, BuyerMeter: buyer.MeterID, KWh: qty,
		PricePerKWh: price.PricePerKWh, Currency: price.Currency,
		OriginRegion: in.Origin, ProcessedBy: e.region, MatchedAt: time.Now().UTC(),
	}
	m.Total = domain.Round(qty*price.PricePerKWh, 2)
	ev, _ := platform.NewEvent(domain.TypeMatched, "//"+e.region+"/matching-engine", m)
	ev.ID = m.MatchID
	ev.Region = in.Origin

	// registrar el calce (vista de lectura) y luego publicar
	raw, _ := json.Marshal(m)
	if err := e.rdb.Set(ctx, "match:"+m.MatchID, raw, time.Hour).Err(); err != nil {
		return err
	}
	if err := platform.Publish(ctx, e.kafka, domain.TopicMatched, m.MatchID, ev); err != nil {
		return fmt.Errorf("publicar EnergyMatched: %w", err)
	}
	n := e.matches.Add(1)
	platform.Log(ctx).Info("EnergyMatched", "match", m.MatchID, "kwh", m.KWh, "price", m.PricePerKWh,
		"origin", m.OriginRegion, "processed_by", m.ProcessedBy, "total_matches", n)
	return nil
}
