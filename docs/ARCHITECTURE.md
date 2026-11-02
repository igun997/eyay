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

## Classification (server side)
After the edge detector writes an event it is `pipeline = queued`. A classifier loop in the worker (`SELECT ... FOR UPDATE
SKIP LOCKED`) crops the snapshot around `bbox` (+15% margin) and asks a System One decision model for one `choice`
question with options `person` / `vehicle` / `false_alarm` via `POST {CLASSIFIER_URL}/v1/systemone` (Ollama, model `d1`
= Liquid AI d1-3B, text+image, ~3 s/image on 4 CPU cores). The answer is stored on the event (`cls_*`) and shown in
triage as `label | confidence | CLASSIFIER` next to the edge result. It never dismisses anything: a person still
confirms or dismisses. No classifier configured -> `skipped`; classifier down -> `failed` (retry later); either way the
event reaches triage.

## Behaviour rules (suspicious behaviour / fraud indicators)
`behavior_rules` ships a default pack (weapon-like object, tampering, concealing an item, climbing a barrier, face covered,
lingering; face-covered and licence-plate-obscured are off by default because they proved noisy or angle-dependent). Classifier rules add one `noul` (yes/no) question each to the same
`/v1/systemone` request as the class question, so one forward pass over the snapshot answers all of them. A rule triggers at
P(yes) >= its threshold. Every evaluated rule is stored in `events.cls_flags`; a trigger sets `events.suspicious`, raises the
event and incident severity to the rule's severity, adds a `system` timeline entry and, if the rule says so, opens an incident
even for categories that normally do not. These are indicators for a person to review. The UI says "indicator", never "fraud",
and nothing is blocked or accused automatically. Probabilities come from a 3B model looking at stills: expect false
positives and misses; thresholds are per rule and meant to be tuned on the site's own footage.

## Out of scope (by decision)
Sensors (MQTT/Modbus), VLM, server classifier, shift reports, NVR playback/export, SSO. No actuators.
