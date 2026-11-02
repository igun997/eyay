-- Engine configuration editable from the dashboard (docs/ENGINE_CONFIG.md).
--   engine_settings 'defaults'  global defaults, edited by admins
--   sources.config              per-camera overrides, merged over the defaults leaf by leaf (absent / null = inherit)
-- The worker re-reads both every poll (EYEAI_POLL_S, 5 s); everything except detector.intra_threads applies without reconnecting.
ALTER TABLE sources ADD COLUMN IF NOT EXISTS config jsonb NOT NULL DEFAULT '{}';

CREATE TABLE IF NOT EXISTS engine_settings (
  key         text PRIMARY KEY,
  value       jsonb NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  updated_by  uuid REFERENCES users(id)
);

INSERT INTO engine_settings (key, value) VALUES ('defaults', '{}') ON CONFLICT (key) DO NOTHING;

-- Estimated speed of the tracked object (km/h), set when a track crosses the calibrated geometry (docs/ENGINE_CONFIG.md "Speed").
ALTER TABLE events ADD COLUMN IF NOT EXISTS speed_kmh real;
