-- Server-side classification of each event snapshot (System One decision model, e.g. Liquid AI d1 served by Ollama).
-- pipeline: queued -> classifying -> ready            (classifier ran)
--                                 -> failed           (classifier unreachable / errored; event still goes to triage)
--           queued -> skipped                         (no classifier configured)
ALTER TABLE events
  ADD COLUMN IF NOT EXISTS pipeline       text NOT NULL DEFAULT 'queued'
      CHECK (pipeline IN ('queued','classifying','ready','failed','skipped')),
  ADD COLUMN IF NOT EXISTS cls_label      text CHECK (cls_label IN ('person','vehicle','false_alarm')),
  ADD COLUMN IF NOT EXISTS cls_confidence real,      -- probability of cls_label
  ADD COLUMN IF NOT EXISTS cls_probs      jsonb,     -- {"person":0.03,"vehicle":0.01,"false_alarm":0.96}
  ADD COLUMN IF NOT EXISTS cls_model      text,
  ADD COLUMN IF NOT EXISTS cls_ms         integer,
  ADD COLUMN IF NOT EXISTS cls_error      text,
  ADD COLUMN IF NOT EXISTS cls_at         timestamptz;
CREATE INDEX IF NOT EXISTS events_pipeline_idx ON events (pipeline, detected_at) WHERE pipeline IN ('queued','classifying');

ALTER TABLE worker_heartbeats
  ADD COLUMN IF NOT EXISTS classifier text;           -- e.g. "d1 @ http://classifier:11434" or NULL when disabled
