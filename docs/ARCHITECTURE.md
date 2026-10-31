# EyeAI live platform: architecture

```
admin (web) --register rtsp/m3u8--> Postgres <--poll sources-- worker --YOLOX--> events/incidents/snapshots
                                        ^                          \--MJPEG with boxes (:8090/live/<id>)
 security (web) <---- Next.js <---------+--- proxy /api/live/<id> (session + role checked)
```

- **web** (Next.js 15, shadcn/ui, Tailwind): auth, roles `admin` and `security`, all screens.
- **worker** (Python): polls `sources` every 5 s, one thread per enabled source, reuses `eyeai.detect` (YOLOX Apache-2.0,
  IoU tracker). Writes `events` (+`event_snapshots`, `incidents`, `incident_timeline`) straight to Postgres and serves the
  annotated live MJPEG. Updates `sources.status/fps/last_seen`, `worker_heartbeats`.
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

## Classification (server side)
After the edge detector writes an event it is `pipeline = queued`. A classifier loop in the worker (`SELECT ... FOR UPDATE
SKIP LOCKED`) crops the snapshot around `bbox` (+15% margin) and asks a System One decision model for one `choice`
question with options `person` / `vehicle` / `false_alarm` via `POST {CLASSIFIER_URL}/v1/systemone` (Ollama, model `d1`
= Liquid AI d1-3B, text+image, ~3 s/image on 4 CPU cores). The answer is stored on the event (`cls_*`) and shown in
triage as `label | confidence | CLASSIFIER` next to the edge result. It never dismisses anything: a person still
confirms or dismisses. No classifier configured -> `skipped`; classifier down -> `failed` (retry later); either way the
event reaches triage.

## Out of scope (by decision)
Sensors (MQTT/Modbus), VLM, server classifier, shift reports, NVR playback/export, SSO. No actuators.
