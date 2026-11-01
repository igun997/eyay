-- EyeAI live platform schema. Shared contract between web/ (Next.js) and worker/ (Python).
-- Applied idempotently by `web` (scripts/migrate) on boot; the worker only waits for it.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS users (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email         text NOT NULL UNIQUE,
  name          text NOT NULL,
  password_hash text NOT NULL,
  role          text NOT NULL CHECK (role IN ('admin','security')),
  active        boolean NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- What counts as "interesting". `label` is a detector class (COCO name, e.g. person, car).
CREATE TABLE IF NOT EXISTS categories (
  label           text PRIMARY KEY,
  title           text NOT NULL,
  severity        text NOT NULL CHECK (severity IN ('CRITICAL','HIGH','MEDIUM','LOW')),
  opens_incident  boolean NOT NULL DEFAULT true,
  cooldown_s      integer NOT NULL DEFAULT 30,   -- min seconds between events of this label on one source
  enabled         boolean NOT NULL DEFAULT true
);

-- A registered live stream. Admin creates; the worker listens to every enabled row.
CREATE TABLE IF NOT EXISTS sources (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code           text NOT NULL UNIQUE,            -- CAM-01 style, shown in UI
  name           text NOT NULL,                   -- location, e.g. "Dock 2"
  url            text NOT NULL,                   -- rtsp://... or http(s)://....m3u8
  kind           text NOT NULL CHECK (kind IN ('rtsp','m3u8')),
  labels         text[] NOT NULL DEFAULT '{person,car}',  -- categories this source watches
  min_confidence real NOT NULL DEFAULT 0.4,
  enabled        boolean NOT NULL DEFAULT true,
  -- worker-owned runtime state
  status         text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','connecting','online','offline','error')),
  last_error     text,
  last_seen      timestamptz,
  fps            real,
  inference_ms   real,
  created_by     uuid REFERENCES users(id),
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE SEQUENCE IF NOT EXISTS incident_seq START 1;

CREATE TABLE IF NOT EXISTS incidents (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code         text NOT NULL UNIQUE DEFAULT ('INC-' || lpad(nextval('incident_seq')::text, 4, '0')),
  title        text NOT NULL,
  severity     text NOT NULL CHECK (severity IN ('CRITICAL','HIGH','MEDIUM','LOW')),
  status       text NOT NULL DEFAULT 'open' CHECK (status IN ('open','acknowledged','resolved')),
  source_id    uuid REFERENCES sources(id) ON DELETE SET NULL,
  label        text,
  assigned_to  uuid REFERENCES users(id),
  opened_at    timestamptz NOT NULL DEFAULT now(),
  resolved_at  timestamptz,
  resolved_by  uuid REFERENCES users(id)
);

-- One row per detection that entered a category. Inserted directly by the worker.
CREATE TABLE IF NOT EXISTS events (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code          text NOT NULL UNIQUE DEFAULT ('evt_' || substr(md5(random()::text), 1, 6)),
  source_id     uuid NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  incident_id   uuid REFERENCES incidents(id) ON DELETE SET NULL,
  label         text NOT NULL,
  confidence    real NOT NULL,
  severity      text NOT NULL CHECK (severity IN ('CRITICAL','HIGH','MEDIUM','LOW')),
  track_id      integer,
  bbox          jsonb,                           -- {x,y,w,h} normalised 0..1 on the snapshot
  title         text NOT NULL,
  -- triage: the incident already exists; triage only re-checks the classification
  status        text NOT NULL DEFAULT 'ready_for_triage' CHECK (status IN ('ready_for_triage','confirmed','dismissed')),
  dismiss_reason text,
  note          text,
  reviewed_by   uuid REFERENCES users(id),
  reviewed_at   timestamptz,
  detected_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS events_status_idx ON events (status, detected_at DESC);
CREATE INDEX IF NOT EXISTS events_source_idx ON events (source_id, detected_at DESC);
CREATE INDEX IF NOT EXISTS events_incident_idx ON events (incident_id);

-- JPEG snapshots with the box already drawn is NOT stored: raw frame + bbox, UI overlays the box.
CREATE TABLE IF NOT EXISTS event_snapshots (
  event_id  uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  idx       smallint NOT NULL DEFAULT 0,
  jpeg      bytea NOT NULL,
  width     integer NOT NULL,
  height    integer NOT NULL,
  PRIMARY KEY (event_id, idx)
);

CREATE TABLE IF NOT EXISTS incident_timeline (
  id          bigserial PRIMARY KEY,
  incident_id uuid NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
  kind        text NOT NULL,                      -- camera | triage | operator | note | system
  title       text NOT NULL,
  detail      text,
  user_id     uuid REFERENCES users(id),
  event_id    uuid REFERENCES events(id) ON DELETE SET NULL,
  at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS timeline_incident_idx ON incident_timeline (incident_id, at);

CREATE TABLE IF NOT EXISTS worker_heartbeats (
  worker_id   text PRIMARY KEY,
  last_seen   timestamptz NOT NULL DEFAULT now(),
  sources     integer NOT NULL DEFAULT 0,
  model       text
);

CREATE TABLE IF NOT EXISTS audit_log (
  id         bigserial PRIMARY KEY,
  user_id    uuid REFERENCES users(id),
  action     text NOT NULL,
  target     text,
  detail     jsonb,
  at         timestamptz NOT NULL DEFAULT now()
);

INSERT INTO categories (label, title, severity, opens_incident, cooldown_s) VALUES
  ('person',     'Person',     'HIGH',   true,  30),
  ('car',        'Car',        'MEDIUM', true,  60),
  ('truck',      'Truck',      'MEDIUM', true,  60),
  ('bus',        'Bus',        'LOW',    false, 60),
  ('motorcycle', 'Motorcycle', 'LOW',    false, 60),
  ('bicycle',    'Bicycle',    'LOW',    false, 60)
ON CONFLICT (label) DO NOTHING;
