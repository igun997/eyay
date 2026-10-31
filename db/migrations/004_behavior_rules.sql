-- Default behaviour rules ("suspicious behaviour / fraud indicators"). They are INDICATORS for a human to review,
-- never verdicts: a System One model answers yes/no probabilities about a snapshot, nothing here accuses anyone.
--   kind 'classifier' : one extra `noul` question asked in the same /v1/systemone request as the class question
--   kind 'indication' : derived from the worker's snapshot-burst indication (no model call)
CREATE TABLE IF NOT EXISTS behavior_rules (
  key            text PRIMARY KEY,
  title          text NOT NULL,
  description    text NOT NULL,
  kind           text NOT NULL CHECK (kind IN ('classifier','indication')),
  question       text,                                   -- classifier rules: the yes/no question put to the model
  applies_to     text[] NOT NULL DEFAULT '{person}',     -- event labels it is evaluated for: person, car, truck, bus, motorcycle, bicycle
  threshold      real NOT NULL DEFAULT 0.75 CHECK (threshold > 0 AND threshold <= 1),   -- P(yes) at or above this triggers
  config         jsonb NOT NULL DEFAULT '{}',            -- indication rules: {"indication_kind":"lingering","min_burst_ms":5000}
  severity       text NOT NULL CHECK (severity IN ('CRITICAL','HIGH','MEDIUM','LOW')),
  opens_incident boolean NOT NULL DEFAULT true,
  enabled        boolean NOT NULL DEFAULT true,
  position       smallint NOT NULL DEFAULT 0
);

INSERT INTO behavior_rules (key, title, description, kind, question, applies_to, threshold, config, severity, opens_incident, enabled, position) VALUES
 ('weapon', 'Weapon-like object', 'A person holds a gun, knife, crowbar or similar object.', 'classifier',
  'Is the person holding a weapon or a weapon-like object such as a gun, knife or crowbar?', '{person}', 0.70, '{}', 'CRITICAL', true, true, 1),
 ('tampering', 'Tampering with equipment, lock or door', 'A person forces, picks or interferes with a door, lock, gate, machine or camera.', 'classifier',
  'Is the person forcing, picking or tampering with a door, lock, gate, machine or camera?', '{person}', 0.75, '{}', 'HIGH', true, true, 2),
 ('concealment', 'Concealing an item', 'A person hides an item in a bag or clothing (possible theft).', 'classifier',
  'Is the person hiding or concealing an item in a bag, jacket or clothing?', '{person}', 0.75, '{}', 'HIGH', true, true, 3),
 ('trespass', 'Climbing or crossing a barrier', 'A person climbs a fence, wall or gate.', 'classifier',
  'Is the person climbing over a fence, wall, gate or barrier?', '{person}', 0.75, '{}', 'HIGH', true, true, 4),
 ('face_covered', 'Face covered or disguised', 'Mask, balaclava or hood hiding the face.', 'classifier',
  'Is the person''s face covered or disguised with a mask, balaclava or a hood pulled over the face?', '{person}', 0.75, '{}', 'MEDIUM', true, true, 5),
 ('lingering', 'Lingering in view', 'A person stays in place for the whole snapshot burst (possible loitering or casing).', 'indication',
  NULL, '{person}', 0.75, '{"indication_kind":"lingering","min_burst_ms":5000}', 'LOW', false, true, 6),
 ('plate_obscured', 'Licence plate covered or obscured', 'Plate hidden or missing. Off by default: depends heavily on camera angle.', 'classifier',
  'Is the vehicle''s licence plate covered, missing or deliberately obscured?', '{car,truck,motorcycle}', 0.85, '{}', 'MEDIUM', true, false, 7)
ON CONFLICT (key) DO NOTHING;

ALTER TABLE events
  ADD COLUMN IF NOT EXISTS cls_flags  jsonb   NOT NULL DEFAULT '[]',   -- every evaluated rule: [{"rule","title","prob","threshold","severity","triggered"}]
  ADD COLUMN IF NOT EXISTS suspicious boolean NOT NULL DEFAULT false;  -- at least one rule triggered
CREATE INDEX IF NOT EXISTS events_suspicious_idx ON events (detected_at DESC) WHERE suspicious;
