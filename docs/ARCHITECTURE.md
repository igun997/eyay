# EyeAI live platform: architecture

```
admin (web) --register rtsp/m3u8--> Postgres <--poll sources-- worker --YOLOX--> events/incidents/snapshots
                                        ^                          \--MJPEG with boxes (:8090/live/<id>)
 security (web) <---- Next.js <---------+--- proxy /api/live/<id> (session + role checked)
```

- **web** (Next.js 15, shadcn/ui, Tailwind): auth, roles `admin` and `security`, all screens.
- **worker** (Rust, `engine/`): polls `sources` every 5 s, one thread per enabled source: ffmpeg decode -> YOLOX (ONNX Runtime,
  Apache-2.0 weights) -> per-label IoU tracker -> events. Writes `events` (+`event_snapshots`, `incidents`, `incident_timeline`)
  straight to Postgres, runs the classifier loop, and serves the annotated live MJPEG (boxes, speed, calibration geometry).
  Updates `sources.status/fps/last_seen`, `worker_heartbeats`. Detector, tracker and speed calibration are configured from the
  dashboard ([ENGINE_CONFIG.md](ENGINE_CONFIG.md)) and hot-reload within one poll. It grew from the proof of concept
  jenggo/poc-yolo-coco (decoder, speed estimators) and replaced the earlier Python worker.
- **db**: `db/migrations/*.sql` is the contract; web applies it on boot.
- **mediamtx** (dev only, compose profile `dev`): local RTSP server fed by `ffmpeg` with a sample video, for live testing without a camera.

## Event flow
1. Worker detects a label listed in `sources.labels` and enabled in `categories` with confidence >= `sources.min_confidence`.
2. New track (or cooldown elapsed) -> insert `events` (status `ready_for_triage`) + snapshot.
3. If `categories.opens_incident`: attach to an open incident of the same source+label opened < 5 min ago, else open one
   (severity from category). Timeline entry kind `camera`.
4. Security triages: **confirm** (event `confirmed`, timeline entry) or **dismiss** with reason (event `dismissed`; if every
   event of the incident is dismissed the incident is resolved as false alarm).
5. Security/admin acknowledges, assigns, notes and resolves incidents.

## Speed (optional, per camera)
With a calibration (two lines and a distance, or four road corners) the engine estimates the speed of tracked objects. It is
stored in `events.speed_kmh` and a track over the limit raises the `speeding` indicator through the behaviour-rule path (flag,
suspicious, timeline, incident if there was none). Time comes from the stream's frame counter, never the wall clock. See
[ENGINE_CONFIG.md](ENGINE_CONFIG.md); the numbers are estimates for a person to review and scale linearly with `distance_m`.

## Snapshot burst and indication
The first snapshot is stored with the event. For `categories.burst_s` seconds the worker keeps sampling the same track
and stores up to `categories.burst_frames` snapshots in total (`event_snapshots`, each with its own `bbox` and
`offset_ms`): a person gets longer than a passing car. Still no video. When the burst ends (`burst_done = true`) the
worker writes an **indication** from the track: `moving` (centre moved more than 8% of the frame width), `lingering`
(stayed put for the whole burst), `crowd` (3+ objects of the label in frame) or `appeared` (seen once, burst too short),
a one-line `indication` sentence and `burst_ms`. The same sentence is added to the incident timeline.

## Classification
The detector (the camera AI) classifies every event when it is written: `events.cls_label` is `person`, `vehicle` or `other`,
`cls_confidence` is the detector's certainty and `cls_model` is `detector`, so triage shows `label | certainty | EDGE` immediately and
there is no waiting for a second model. A person still confirms or dismisses; nothing is ever dismissed automatically.

An **optional second AI check** (`CLASSIFIER_URL`: a System One decision model served by Ollama, e.g. Liquid AI `d1`) only answers the
yes/no behaviour questions of rules of kind `classifier`. When it is not configured, those rules stay silent and events are `ready`
at once; when it is, events that have such questions are `queued`, checked in the background and then `ready` (or `failed`, with the
detector's classification untouched).

## Behaviour rules (suspicious behaviour / fraud indicators)
`behavior_rules` ships a default pack (weapon-like object, tampering, concealing an item, climbing a barrier, face covered,
lingering; face-covered and licence-plate-obscured are off by default because they proved noisy or angle-dependent). Rules come in three kinds: `classifier` (yes/no question to the optional second AI check), `indication` (from the follow-up photos) and `object` (the detector sees a knife, scissors or bat next to a person; works with the cameras alone). Classifier rules add one `noul` (yes/no) question each to the same
`/v1/systemone` request as the class question, so one forward pass over the snapshot answers all of them. A rule triggers at
P(yes) >= its threshold. Every evaluated rule is stored in `events.cls_flags`; a trigger sets `events.suspicious`, raises the
event and incident severity to the rule's severity, adds a `system` timeline entry and, if the rule says so, opens an incident
even for categories that normally do not. These are indicators for a person to review. The UI says "indicator", never "fraud",
and nothing is blocked or accused automatically. Probabilities come from a 3B model looking at stills: expect false
positives and misses; thresholds are per rule and meant to be tuned on the site's own footage.

## Alerts and automations
Admins build "when event A happens, simulate or send message B" in the dashboard (**Alerts**). The worker evaluates each event the moment it is
created or changes and, for automations in *live* mode, sends to a webhook, an MQTT broker or a PLC (Modbus TCP coil pulse or register
write); in *practice* (simulate) mode it only logs what would be sent. Quiet period, hourly cap, per-action timeouts and a full run log keep
it safe; coil writes always switch back off. It is for alerts and indications, never for safety functions. See
[AUTOMATIONS.md](AUTOMATIONS.md).

## Out of scope (by decision)
Sensor ingestion (MQTT/Modbus *input*), a general VLM, shift reports, NVR playback/export, SSO. Outputs exist only through automations,
simulation first.
