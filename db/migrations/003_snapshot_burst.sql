-- Snapshot burst + indication. For every event the worker keeps capturing a few snapshots over a few seconds
-- (never video), as many and as long as the category needs, and says what the burst shows.
ALTER TABLE categories
  ADD COLUMN IF NOT EXISTS burst_s      integer  NOT NULL DEFAULT 4 CHECK (burst_s BETWEEN 0 AND 60),        -- 0 = single snapshot
  ADD COLUMN IF NOT EXISTS burst_frames smallint NOT NULL DEFAULT 3 CHECK (burst_frames BETWEEN 1 AND 12);
UPDATE categories SET burst_s = 6, burst_frames = 4 WHERE label = 'person' AND burst_s = 4 AND burst_frames = 3;  -- people: look longer
UPDATE categories SET burst_s = 3, burst_frames = 3 WHERE label IN ('car','truck','bus','motorcycle','bicycle') AND burst_s = 4 AND burst_frames = 3;

-- one box per snapshot (the object moves between frames), and when it was taken relative to the event
ALTER TABLE event_snapshots
  ADD COLUMN IF NOT EXISTS bbox      jsonb,                          -- {x,y,w,h} normalised, NULL if the object was not visible in that frame
  ADD COLUMN IF NOT EXISTS offset_ms integer NOT NULL DEFAULT 0;     -- ms after events.detected_at

-- what the burst shows. kind: appeared | moving | lingering | crowd
ALTER TABLE events
  ADD COLUMN IF NOT EXISTS indication_kind text CHECK (indication_kind IN ('appeared','moving','lingering','crowd')),
  ADD COLUMN IF NOT EXISTS indication      text,                     -- human sentence, e.g. "Person lingering in view for 5.2 s"
  ADD COLUMN IF NOT EXISTS burst_ms        integer,                  -- actual duration covered by the snapshots
  ADD COLUMN IF NOT EXISTS burst_done      boolean NOT NULL DEFAULT false;  -- false while the worker is still capturing frames
