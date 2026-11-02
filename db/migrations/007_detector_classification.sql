-- The camera AI (the detector itself) now classifies every event: cls_label / cls_confidence / cls_model='detector' are filled when
-- the event is written. The server-side question model becomes optional and only answers behaviour-rule questions.
-- 'other' covers watched labels that are neither a person nor a vehicle (e.g. a dog).
ALTER TABLE events DROP CONSTRAINT IF EXISTS events_cls_label_check;
ALTER TABLE events ADD CONSTRAINT events_cls_label_check CHECK (cls_label IN ('person','vehicle','false_alarm','other'));

-- New rule kind 'object': a detector label (knife, scissors, bat, ...) seen next to a person during the follow-up photos.
--   config: {"objects": ["knife","scissors","baseball bat"], "margin": 0.3}   margin = how far outside the person box counts as "next to"
--   threshold = minimum detector certainty of the object (0..1)
ALTER TABLE behavior_rules DROP CONSTRAINT IF EXISTS behavior_rules_kind_check;
ALTER TABLE behavior_rules ADD CONSTRAINT behavior_rules_kind_check CHECK (kind IN ('classifier','indication','object'));

INSERT INTO behavior_rules (key, title, description, kind, question, applies_to, threshold, config, severity, opens_incident, enabled, position) VALUES
 ('weapon_object', 'Knife or blade-like object near a person',
  'The camera AI sees a knife, scissors or bat next to a person. Works without the second AI check; small or hidden objects are often missed, and kitchen or office scenes can cause false warnings.',
  'object', NULL, '{person}', 0.35, '{"objects":["knife","scissors","baseball bat"],"margin":0.3}', 'HIGH', true, true, 0)
ON CONFLICT (key) DO NOTHING;
