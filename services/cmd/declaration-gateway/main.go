// declaration-gateway: consume la Cola de Declaración de Posición, persiste en
// SQLite local (tabla declarations + outbox en la misma transacción) y un relay
// publica PositionDeclared. Escala 0..N con KEDA según el lag del grupo.
package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/trace"
	_ "modernc.org/sqlite"

	"github.com/csbenitez/voltera-eda/internal/domain"
	"github.com/csbenitez/voltera-eda/internal/platform"
)

const schema = `
CREATE TABLE IF NOT EXISTS declarations (
  id TEXT PRIMARY KEY, meter_id TEXT, kind TEXT, kwh REAL, measured_at TEXT, received_at TEXT);
CREATE TABLE IF NOT EXISTS outbox (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT, msg_key TEXT, event TEXT,
  traceparent TEXT, sent INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS forwarded (id TEXT PRIMARY KEY, home_region TEXT, received_at TEXT);
CREATE INDEX IF NOT EXISTS outbox_pending ON outbox(sent, seq);`

func main() {
	platform.Run("declaration-gateway", func(ctx context.Context) error {
		db, err := sql.Open("sqlite", platform.Env("DB_PATH", "/data/gateway.db")+"?_pragma=journal_mode(WAL)&_pragma=busy_timeout(5000)")
		if err != nil {
			return err
		}
		defer db.Close()
		db.SetMaxOpenConns(1)
		if _, err := db.Exec(schema); err != nil {
			return err
		}

		consumer, err := platform.NewKafka(platform.ConsumerOpts("declaration-gateway", domain.TopicIngress)...)
		if err != nil {
			return err
		}
		defer consumer.Close()
		producer, err := platform.NewKafka()
		if err != nil {
			return err
		}
		defer producer.Close()

		go func() { _ = platform.Serve(ctx, nil) }()
		relayDone := make(chan struct{})
		go func() { relay(ctx, db, producer); close(relayDone) }()

		err = platform.Consume(ctx, consumer, 3, func(ctx context.Context, ev platform.Event, _ *kgo.Record) error {
			return ingest(ctx, db, ev)
		})
		<-relayDone
		return err
	})
}

// ingest valida y guarda declaración + evento outbox de forma atómica e idempotente.
func ingest(ctx context.Context, db *sql.DB, in platform.Event) error {
	var d domain.Declaration
	if err := in.Decode(&d); err != nil {
		platform.Log(ctx).Warn("declaración inválida (json)", "id", in.ID, "err", err)
		return nil // no reintentar basura
	}
	if err := d.Validate(); err != nil {
		platform.Log(ctx).Warn("declaración inválida", "id", in.ID, "err", err)
		return nil
	}
	if home := domain.HomeRegion(d.MeterID); home != "" && home != platform.Region() {
		return forward(ctx, db, in, d, home)
	}
	pos := domain.PositionDeclared{
		PositionID: in.ID, MeterID: d.MeterID, Kind: d.Kind, KWh: d.KWh,
		DeclaredAt: time.Now().UTC(), Region: platform.Region(),
	}
	out, err := platform.NewEvent(domain.TypePosition, "//"+platform.Region()+"/declaration-gateway", pos)
	if err != nil {
		return err
	}
	out.ID = in.ID // misma identidad de negocio de punta a punta
	evJSON, _ := json.Marshal(out)

	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	res, err := tx.Exec(`INSERT INTO declarations VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING`,
		in.ID, d.MeterID, d.Kind, d.KWh, d.MeasuredAt.Format(time.RFC3339Nano), pos.DeclaredAt.Format(time.RFC3339Nano))
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		platform.Log(ctx).Info("declaración duplicada ignorada", "id", in.ID)
		return tx.Commit()
	}
	if _, err := tx.Exec(`INSERT INTO outbox(topic,msg_key,event,traceparent) VALUES (?,?,?,?)`,
		domain.TopicPositions, d.MeterID, string(evJSON), platform.TraceparentOf(ctx)); err != nil {
		return err
	}
	platform.Log(ctx).Info("declaración persistida", "id", in.ID, "meter", d.MeterID, "kind", d.Kind, "kwh", d.KWh)
	return tx.Commit()
}

// forward reencamina al outbox (topic de Knative) una declaración de un medidor
// que pertenece a otra región. No genera posición local: la región dueña la
// procesará con el mismo ce-id. La extensión homeregion es la que filtra el Trigger.
func forward(ctx context.Context, db *sql.DB, in platform.Event, d domain.Declaration, home string) error {
	out, err := platform.NewEvent(domain.TypeForwarded, "//"+platform.Region()+"/declaration-gateway", d)
	if err != nil {
		return err
	}
	out.ID = in.ID
	// Span explícito del salto: en App Insights es el último eslabón visible en la región
	// origen antes de que Knative (KafkaSource -> Broker -> Trigger -> KafkaSink) tome el evento.
	ctx, span := platform.Tracer().Start(ctx, "knative forward "+home,
		trace.WithAttributes(
			attribute.String("messaging.message.id", in.ID),
			attribute.String("voltera.channel", "knative"),
			attribute.String("voltera.origin_region", platform.Region()),
			attribute.String("voltera.home_region", home)))
	defer span.End()
	// traceparent como extensión CE: es como Knative lo transporta entre sus componentes.
	out.Ext = map[string]string{"homeregion": home, "originregion": platform.Region(),
		"traceparent": platform.TraceparentOf(ctx)}
	evJSON, _ := json.Marshal(out)

	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	res, err := tx.Exec(`INSERT INTO forwarded VALUES (?,?,?) ON CONFLICT(id) DO NOTHING`,
		in.ID, home, time.Now().UTC().Format(time.RFC3339Nano))
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		platform.Log(ctx).Info("reenvío duplicado ignorado", "id", in.ID)
		return tx.Commit()
	}
	if _, err := tx.Exec(`INSERT INTO outbox(topic,msg_key,event,traceparent) VALUES (?,?,?,?)`,
		domain.TopicForward, d.MeterID, string(evJSON), platform.TraceparentOf(ctx)); err != nil {
		return err
	}
	platform.Log(ctx).Info("declaración reenviada a otra región", "id", in.ID, "meter", d.MeterID, "home", home)
	return tx.Commit()
}

// relay publica los eventos pendientes del outbox. Al recibir SIGTERM (KEDA
// escalando a 0) vacía lo pendiente antes de salir.
func relay(ctx context.Context, db *sql.DB, cl *kgo.Client) {
	tick := time.NewTicker(200 * time.Millisecond)
	defer tick.Stop()
	for {
		select {
		case <-ctx.Done():
			c, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			_ = flush(c, db, cl)
			cancel()
			return
		case <-tick.C:
			if err := flush(ctx, db, cl); err != nil && ctx.Err() == nil {
				platform.Log(ctx).Warn("outbox relay", "err", err)
			}
		}
	}
}

func flush(ctx context.Context, db *sql.DB, cl *kgo.Client) error {
	rows, err := db.QueryContext(ctx, `SELECT seq, topic, msg_key, event, traceparent FROM outbox WHERE sent=0 ORDER BY seq LIMIT 200`)
	if err != nil {
		return err
	}
	type item struct {
		seq                int64
		topic, key, ev, tp string
	}
	var items []item
	for rows.Next() {
		var it item
		if err := rows.Scan(&it.seq, &it.topic, &it.key, &it.ev, &it.tp); err != nil {
			rows.Close()
			return err
		}
		items = append(items, it)
	}
	rows.Close()

	for _, it := range items {
		var ev platform.Event
		if err := json.Unmarshal([]byte(it.ev), &ev); err != nil {
			return fmt.Errorf("outbox %d corrupto: %w", it.seq, err)
		}
		pctx := platform.ContextFromTraceparent(ctx, it.tp)
		if err := platform.Publish(pctx, cl, it.topic, it.key, ev); err != nil {
			return err
		}
		if _, err := db.ExecContext(ctx, `UPDATE outbox SET sent=1 WHERE seq=?`, it.seq); err != nil {
			return err
		}
	}
	return nil
}
