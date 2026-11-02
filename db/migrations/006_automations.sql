-- Alerts and automations (docs/AUTOMATIONS.md): "when this happens, simulate or send that" to a webhook, MQTT broker or PLC.
--   connections      where messages go (secrets are stored as given; the API never returns them)
--   automations      the if / then rules, SIMULATE by default
--   automation_runs  one row per firing or test: what matched, what was (or would have been) sent, how it went
CREATE TABLE IF NOT EXISTS connections (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name        text NOT NULL UNIQUE,
  kind        text NOT NULL CHECK (kind IN ('webhook','mqtt','modbus')),
  config      jsonb NOT NULL DEFAULT '{}',
  enabled     boolean NOT NULL DEFAULT true,
  created_by  uuid REFERENCES users(id),
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS automations (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name         text NOT NULL,
  description  text NOT NULL DEFAULT '',
  enabled      boolean NOT NULL DEFAULT false,
  mode         text NOT NULL DEFAULT 'simulate' CHECK (mode IN ('simulate','live')),
  conditions   jsonb NOT NULL DEFAULT '[]',
  actions      jsonb NOT NULL DEFAULT '[]',
  cooldown_s   integer NOT NULL DEFAULT 60 CHECK (cooldown_s BETWEEN 0 AND 86400),   -- min seconds between firings per camera
  max_per_hour integer NOT NULL DEFAULT 30 CHECK (max_per_hour BETWEEN 1 AND 1000),  -- hard cap; extra firings are logged as skipped
  position     smallint NOT NULL DEFAULT 0,
  created_by   uuid REFERENCES users(id),
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS automation_runs (
  id            bigserial PRIMARY KEY,
  automation_id uuid REFERENCES automations(id) ON DELETE CASCADE,       -- NULL for a connection test
  connection_id uuid REFERENCES connections(id) ON DELETE CASCADE,       -- set for a connection test
  event_id      uuid REFERENCES events(id) ON DELETE SET NULL,           -- NULL for tests
  source_id     uuid REFERENCES sources(id) ON DELETE SET NULL,
  mode          text NOT NULL CHECK (mode IN ('simulate','live','test_send','test_simulate')),
  status        text NOT NULL CHECK (status IN ('queued','running','sent','simulated','failed','skipped')),
  detail        jsonb NOT NULL DEFAULT '{}',   -- {"reason": "...", "actions": [{"type","target","ok","message","would_send","ms"}]}
  requested_by  uuid REFERENCES users(id),     -- who queued a test
  at            timestamptz NOT NULL DEFAULT now(),
  finished_at   timestamptz
);
-- a real event fires an automation at most once
CREATE UNIQUE INDEX IF NOT EXISTS automation_runs_once ON automation_runs (automation_id, event_id) WHERE event_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS automation_runs_recent ON automation_runs (automation_id, at DESC);
CREATE INDEX IF NOT EXISTS automation_runs_queue ON automation_runs (at) WHERE status = 'queued';
