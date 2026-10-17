package platform

import (
	"context"
	"fmt"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/trace"
)

// NewKafka crea un cliente franz-go contra Redpanda (KAFKA_BROKERS).
func NewKafka(opts ...kgo.Opt) (*kgo.Client, error) {
	brokers := strings.Split(Env("KAFKA_BROKERS", "localhost:9092"), ",")
	base := []kgo.Opt{
		kgo.SeedBrokers(brokers...),
		kgo.ClientID(Env("HOSTNAME", "voltera")),
		kgo.ProducerLinger(5 * time.Millisecond),
	}
	return kgo.NewClient(append(base, opts...)...)
}

// ConsumerOpts suscribe al grupo indicado con commit manual (at-least-once).
func ConsumerOpts(group string, topics ...string) []kgo.Opt {
	return []kgo.Opt{
		kgo.ConsumerGroup(group),
		kgo.ConsumeTopics(topics...),
		kgo.DisableAutoCommit(),
		kgo.ConsumeResetOffset(kgo.NewOffset().AtStart()),
	}
}

// Publish produce el evento de forma síncrona dentro de un span PRODUCER
// (en App Insights aparece como dependency "publish <topic>").
func Publish(ctx context.Context, cl *kgo.Client, topic, key string, ev Event) error {
	ctx, span := Tracer().Start(ctx, "publish "+topic, trace.WithSpanKind(trace.SpanKindProducer),
		trace.WithAttributes(
			attribute.String("messaging.system", "kafka"),
			attribute.String("messaging.destination.name", topic),
			attribute.String("messaging.message.id", ev.ID),
			attribute.String("cloudevents.event_type", ev.Type),
			attribute.String("voltera.origin_region", ev.Region),
		))
	defer span.End()
	if err := cl.ProduceSync(ctx, ev.ToRecord(ctx, topic, key)).FirstErr(); err != nil {
		span.RecordError(err)
		span.SetStatus(codes.Error, err.Error())
		return fmt.Errorf("publish %s: %w", topic, err)
	}
	return nil
}

// Handler procesa un evento; el ctx ya contiene el span CONSUMER.
type Handler func(ctx context.Context, ev Event, r *kgo.Record) error

// Consume ejecuta el loop de consumo. maxRetries < 0 reintenta indefinidamente
// (bloquea la partición y no hace commit: usado por el mesh-bridge para no perder
// eventos si la nube no está disponible). Con maxRetries >= 0 el evento se
// descarta tras agotar los reintentos (se registra el error).
func Consume(ctx context.Context, cl *kgo.Client, maxRetries int, h Handler) error {
	for {
		fetches := cl.PollFetches(ctx)
		if ctx.Err() != nil {
			return nil
		}
		fetches.EachError(func(t string, p int32, err error) {
			Log(ctx).Warn("fetch error", "topic", t, "partition", p, "err", err)
		})
		var stop bool
		fetches.EachRecord(func(r *kgo.Record) {
			if stop {
				return
			}
			if !handleWithRetry(ctx, r, maxRetries, h) {
				stop = true
			}
		})
		if stop {
			return nil
		}
		if err := cl.CommitUncommittedOffsets(ctx); err != nil && ctx.Err() == nil {
			Log(ctx).Warn("commit failed", "err", err)
		}
	}
}

// ConsumeParallel procesa cada fetch con hasta `workers` goroutines y hace
// commit solo cuando todo el lote terminó. Sin garantía de orden entre eventos.
func ConsumeParallel(ctx context.Context, cl *kgo.Client, workers, maxRetries int, h Handler) error {
	sem := make(chan struct{}, max(1, workers))
	for {
		fetches := cl.PollFetches(ctx)
		if ctx.Err() != nil {
			return nil
		}
		fetches.EachError(func(t string, p int32, err error) {
			Log(ctx).Warn("fetch error", "topic", t, "partition", p, "err", err)
		})
		var wg sync.WaitGroup
		var aborted atomic.Bool
		fetches.EachRecord(func(r *kgo.Record) {
			sem <- struct{}{}
			wg.Add(1)
			go func() {
				defer func() { <-sem; wg.Done() }()
				if !handleWithRetry(ctx, r, maxRetries, h) {
					aborted.Store(true)
				}
			}()
		})
		wg.Wait()
		if aborted.Load() {
			return nil
		}
		if err := cl.CommitUncommittedOffsets(ctx); err != nil && ctx.Err() == nil {
			Log(ctx).Warn("commit failed", "err", err)
		}
	}
}

func handleWithRetry(ctx context.Context, r *kgo.Record, maxRetries int, h Handler) bool {
	backoff := 200 * time.Millisecond
	for attempt := 0; ; attempt++ {
		pctx, ev := FromRecord(ctx, r)
		if Untraced(ev.Type) {
			if err := h(pctx, ev, r); err == nil || (maxRetries >= 0 && attempt >= maxRetries) {
				return true
			}
			select {
			case <-ctx.Done():
				return false
			case <-time.After(backoff):
			}
			continue
		}
		sctx, span := Tracer().Start(pctx, "process "+r.Topic, trace.WithSpanKind(trace.SpanKindConsumer),
			trace.WithAttributes(
				attribute.String("messaging.system", "kafka"),
				attribute.String("messaging.source.name", r.Topic),
				attribute.Int("messaging.kafka.partition", int(r.Partition)),
				attribute.Int64("messaging.kafka.offset", r.Offset),
				attribute.String("messaging.message.id", ev.ID),
				attribute.String("cloudevents.event_type", ev.Type),
				attribute.String("voltera.origin_region", ev.Region),
				attribute.Int("voltera.attempt", attempt),
			))
		err := h(sctx, ev, r)
		if err != nil {
			span.RecordError(err)
			span.SetStatus(codes.Error, err.Error())
		}
		span.End()
		if err == nil {
			return true
		}
		Log(sctx).Warn("handler error", "topic", r.Topic, "id", ev.ID, "attempt", attempt, "err", err)
		if maxRetries >= 0 && attempt >= maxRetries {
			Log(sctx).Error("dropping event after retries", "topic", r.Topic, "id", ev.ID)
			return true
		}
		select {
		case <-ctx.Done():
			return false
		case <-time.After(backoff):
		}
		if backoff < 10*time.Second {
			backoff *= 2
		}
	}
}
