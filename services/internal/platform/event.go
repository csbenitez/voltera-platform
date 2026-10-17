package platform

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/propagation"
)

// Event es un CloudEvent 1.0 en modo binario: los atributos viajan en headers
// (Kafka: ce_*, HTTP: Ce-*) y el payload JSON en el cuerpo. La extensión
// "region" indica la región de origen del hecho de negocio.
type Event struct {
	ID     string          `json:"id"`
	Type   string          `json:"type"`
	Source string          `json:"source"`
	Region string          `json:"region"`
	Time   time.Time       `json:"time"`
	Data   json.RawMessage `json:"data"`
}

func NewID() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

// NewEvent crea un evento originado en la región de este proceso.
func NewEvent(typ, source string, data any) (Event, error) {
	raw, err := json.Marshal(data)
	if err != nil {
		return Event{}, err
	}
	return Event{ID: NewID(), Type: typ, Source: source, Region: Region(), Time: time.Now().UTC(), Data: raw}, nil
}

func (e Event) Decode(v any) error { return json.Unmarshal(e.Data, v) }

// ---- Kafka ----

type recordCarrier struct{ r *kgo.Record }

func (c recordCarrier) Get(k string) string {
	for _, h := range c.r.Headers {
		if h.Key == k {
			return string(h.Value)
		}
	}
	return ""
}
func (c recordCarrier) Set(k, v string) {
	for i, h := range c.r.Headers {
		if h.Key == k {
			c.r.Headers[i].Value = []byte(v)
			return
		}
	}
	c.r.Headers = append(c.r.Headers, kgo.RecordHeader{Key: k, Value: []byte(v)})
}
func (c recordCarrier) Keys() []string {
	out := make([]string, 0, len(c.r.Headers))
	for _, h := range c.r.Headers {
		out = append(out, h.Key)
	}
	return out
}

// ToRecord serializa el evento e inyecta el traceparent del contexto.
func (e Event) ToRecord(ctx context.Context, topic, key string) *kgo.Record {
	r := &kgo.Record{Topic: topic, Key: []byte(key), Value: e.Data}
	c := recordCarrier{r}
	c.Set("ce_specversion", "1.0")
	c.Set("ce_id", e.ID)
	c.Set("ce_type", e.Type)
	c.Set("ce_source", e.Source)
	c.Set("ce_region", e.Region)
	c.Set("ce_time", e.Time.Format(time.RFC3339Nano))
	c.Set("content-type", "application/json")
	otel.GetTextMapPropagator().Inject(ctx, c)
	return r
}

// FromRecord devuelve el evento y el contexto remoto (padre) extraído de los headers.
func FromRecord(ctx context.Context, r *kgo.Record) (context.Context, Event) {
	c := recordCarrier{r}
	t, _ := time.Parse(time.RFC3339Nano, c.Get("ce_time"))
	ev := Event{
		ID: c.Get("ce_id"), Type: c.Get("ce_type"), Source: c.Get("ce_source"),
		Region: c.Get("ce_region"), Time: t, Data: r.Value,
	}
	return otel.GetTextMapPropagator().Extract(ctx, c), ev
}

// ---- HTTP ----

func (e Event) ToHTTP(ctx context.Context, req *http.Request) {
	h := req.Header
	h.Set("Ce-Specversion", "1.0")
	h.Set("Ce-Id", e.ID)
	h.Set("Ce-Type", e.Type)
	h.Set("Ce-Source", e.Source)
	h.Set("Ce-Region", e.Region)
	h.Set("Ce-Time", e.Time.Format(time.RFC3339Nano))
	h.Set("Content-Type", "application/json")
	otel.GetTextMapPropagator().Inject(ctx, propagation.HeaderCarrier(h))
}

func FromHTTP(r *http.Request, body []byte) (context.Context, Event, error) {
	h := r.Header
	t, _ := time.Parse(time.RFC3339Nano, h.Get("Ce-Time"))
	ev := Event{
		ID: h.Get("Ce-Id"), Type: h.Get("Ce-Type"), Source: h.Get("Ce-Source"),
		Region: h.Get("Ce-Region"), Time: t, Data: body,
	}
	if ev.ID == "" || ev.Type == "" {
		return r.Context(), ev, errors.New("missing Ce-Id/Ce-Type")
	}
	return otel.GetTextMapPropagator().Extract(r.Context(), propagation.HeaderCarrier(h)), ev, nil
}
