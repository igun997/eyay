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

## Out of scope (by decision)
Sensors (MQTT/Modbus), VLM, server classifier, shift reports, NVR playback/export, SSO. No actuators.
