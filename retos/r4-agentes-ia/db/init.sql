-- Base del copiloto (datos de demo). Los roles y claves son SOLO de demostración.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE clientes (
  nic            TEXT PRIMARY KEY,
  nombre         TEXT NOT NULL,
  direccion      TEXT NOT NULL,
  medidor        TEXT NOT NULL UNIQUE,   -- AMI-cloud-NNNN (event mesh)
  participant_id TEXT NOT NULL,          -- participante de pagos (service mesh)
  comunidad      TEXT NOT NULL,
  escenario      TEXT NOT NULL
);

CREATE TABLE facturas (
  id_negocio TEXT PRIMARY KEY,           -- FAC-2026-10-NNNNNNN (misma clave que en la cadena)
  nic        TEXT NOT NULL REFERENCES clientes(nic),
  periodo    TEXT NOT NULL,
  wh         BIGINT NOT NULL,
  cop        BIGINT NOT NULL,
  lineas     JSONB NOT NULL,
  estado_cobro TEXT NOT NULL DEFAULT 'pendiente' CHECK (estado_cobro IN ('pendiente','pagada')),
  anclada    BOOLEAN NOT NULL,
  tx_hash    TEXT
);

CREATE TABLE reclamos (
  id         BIGSERIAL PRIMARY KEY,
  nic        TEXT NOT NULL REFERENCES clientes(nic),
  id_negocio TEXT REFERENCES facturas(id_negocio),
  motivo     TEXT NOT NULL,
  prioridad  TEXT NOT NULL DEFAULT 'normal' CHECK (prioridad IN ('normal','alta')),
  creado_en  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Fragmentos del RAG: híbrido (vector + texto completo) con metadatos para filtrar.
CREATE TABLE fragmentos (
  id        BIGSERIAL PRIMARY KEY,
  fuente    TEXT NOT NULL,               -- ruta del documento
  titulo    TEXT NOT NULL,
  tipo      TEXT NOT NULL,               -- politica | tarifa | procedimiento | faq | contrato | tecnico
  nic       TEXT,                        -- NULL = aplica a todos; si no, solo a ese cliente
  contenido TEXT NOT NULL,
  embedding vector(384),
  tsv       tsvector GENERATED ALWAYS AS (to_tsvector('spanish', contenido)) STORED
);
CREATE INDEX fragmentos_tsv ON fragmentos USING gin (tsv);
CREATE INDEX fragmentos_nic ON fragmentos (nic);

CREATE ROLE copiloto_ro LOGIN PASSWORD 'ro-demo';
CREATE ROLE copiloto_rw LOGIN PASSWORD 'rw-demo';
GRANT SELECT ON clientes, facturas, reclamos, fragmentos TO copiloto_ro;
GRANT SELECT ON clientes, facturas, reclamos TO copiloto_rw;
GRANT INSERT ON reclamos TO copiloto_rw;
GRANT USAGE ON SEQUENCE reclamos_id_seq TO copiloto_rw;
