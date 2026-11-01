# eyeai: live RTSP / M3U8 analysis platform

Admins register an RTSP or M3U8 URL; a worker listens, runs YOLOX on the live frames and writes every detection that
falls in a configured category straight into Postgres. Security reviews them in triage and handles incidents. The live
view shows the stream with bounding boxes drawn on it. Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
UI reference: [docs/design-system](docs/design-system/README.md).

| Image | Source | Role |
|---|---|---|
| `eyeai-app` | `docker/web.Dockerfile` (`web/`, Next.js 15 + shadcn/ui) | UI + API, applies DB migrations on boot, seeds the first admin |
| `eyeai-worker` | `docker/worker.Dockerfile` (`worker/`, reuses `src/eyeai/detect`) | stream listener, detector, events/incidents writer, MJPEG with boxes |
| `postgres:16-alpine` | upstream | data |
| `ollama/ollama` (profile `classifier`) | upstream, model **Liquid AI d1-3B** | server-side classification of every snapshot (System One decision model, text+image) |
| `mediamtx` + `jrottenberg/ffmpeg` | upstream, compose profile `dev` only | local RTSP/HLS server fed with `data/samples/*.mp4` for live testing without a camera |

```bash
scripts/fetch_assets.sh                              # sample videos (dev feed only)
docker compose --profile dev up --build              # http://localhost:3000  admin@eyeai.local / admin12345
# Sources & health -> Add source:  rtsp://mediamtx:8554/dock2  |  /gate  |  /aisle
#                                  http://mediamtx:8888/aisle/index.m3u8   (HLS)
CLASSIFIER_URL=http://classifier:11434 docker compose --profile dev --profile classifier up --build   # + d1 classifier (downloads 3.7 GB once)
docker compose up --build                            # production-like: no demo feed, register your own cameras
```

**Classification.** Each detection is queued and sent (cropped around its box) to a System One decision model that answers
one typed question: `person` / `vehicle` / `false_alarm`. Triage shows both results (`EDGE` and `CLASSIFIER`), flags
disagreements, and a person still decides; the classifier never dismisses anything. Model chosen from
[awesome-system-one](https://github.com/andyrewlee/awesome-system-one): **d1-3B** (text+image, native in Ollama `/v1/systemone`; license is **LFM Open License v1.0**, not Apache: review its commercial-use terms before shipping.
Apache-2.0 alternatives on that list: Intern-Decision, dohnuts, Clef), 39/39 on a small crop set at ~3 s/image on 4 CPU cores. Clef-flash (9B) crashed in Ollama's CPU backend on the
test machine. Use any `/v1/systemone` endpoint by setting `CLASSIFIER_URL` / `CLASSIFIER_MODEL`; leave `CLASSIFIER_URL` empty to run without it.

Set `AUTH_SECRET`, `ADMIN_EMAIL`, `ADMIN_PASSWORD` for anything beyond local testing. Roles: `admin` (sources, users,
everything) and `security` (overview, triage, incidents, cameras, rules read-only). Throughput scales with CPU: inference
measured 0.12 s per frame on one idle stream and 0.7-1.7 s per frame with 3 streams sharing 2 cores, so cap it with `EYEAI_TARGET_FPS` and run one worker per ~2 streams per 2 cores.
One worker per database (no source locking yet). Web tests: `cd web && pnpm test`; worker tests: `PYTHONPATH=src:worker pytest worker/tests`.

---

# eyeai: event-first CCTV + IoT engine (POC)

Engine only; the dashboard and the LLM/VLM reporting agent are deliberately not built yet. It implements the
architecture of *Analisis Kelayakan Bisnis AI CCTV Box, IoT, AI Agent (Oct 2026)*:

```
SOURCE            ADAPTER                         DECISION                  RULE ENGINE            STORE
native camera AI  isapi / dahua / onvif / frigate native event if present   dedup, dwell, counts,  sqlite events
RTSP / file       local YOLOX + tracker (gaps)    else local inference      occupancy, after-hours  + audit log
MQTT / Modbus     mqtt_sensor / modbus            (never both)              energy, dock/ANPR/WMS,
WMS/ANPR/POS/...  signed webhook                                            cold chain, machine,
                                                                            queue, health,
                                                                            escalation, incidents
```

The engine **only emits events**. No rule or adapter has any actuator output (PDF section 7 safety limits).

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
scripts/fetch_assets.sh                       # YOLOX (Apache-2.0) models + public sample videos, sha256-checked
PYTHONPATH=src .venv/bin/pytest -q            # 389 tests, 1 opt-in skipped (real MQTT broker)
```

## Use

```bash
python -m eyeai discover 192.168.1.64 --username admin --password ...   # PDF section 1: what does the camera already do?
python -m eyeai run --config config/engine.example.yaml --seconds 30
python -m eyeai events --db data/eyeai.db --limit 50
python -m eyeai.sim --list ; python -m eyeai.sim cold_chain_breach      # replay a use-case scenario
python -m eyeai.bench --video data/samples/car-detection.mp4 --model data/models/yolox_s.onnx --classes car,person --out data/bench
```

`config/engine.example.yaml` and `config/rules.example.yaml` document every key. Unknown keys are errors.
Webhook records (`POST /events`, `X-Signature: sha256=<hmac>`): `wms_order`, `anpr`, `pos_transaction`,
`access_log`, `maintenance_log`, `barcode_scan`; schema in `src/eyeai/adapters/external.py`.

## Use cases covered by rules (each has a simulated scenario and tests)

| Case (PDF section 6 / 10) | Rule | Scenario |
|---|---|---|
| Native + local duplicate suppression | `Deduplicator` | `duplicate_native_local` |
| Vehicle/person dwell, over-capacity | `ZoneDwell`, `OccupancyTracker` | `local_tracking_dwell` |
| Traffic / footfall counting | `LineCountAggregator` | `traffic_counting` |
| Door + access log + person after hours | `AfterHoursAccess` | `after_hours_intrusion` |
| Power meter + occupancy (energy waste) | `EnergyWaste` | `energy_waste_night` |
| Dock / gate + ANPR + WMS | `DockReconciliation` | `warehouse_dock_day`, `gate_anpr_mismatch` |
| Cold chain sensors + visual cause hint | `ColdChain` | `cold_chain_breach` |
| Vibration + operator + maintenance log | `MachineAnomaly` | `machine_vibration_event` |
| Retail queue + POS staffing | `QueueMonitor` | `retail_queue_rush` |
| Camera/sensor offline, slow inference | `DeviceHealth` | `camera_outage` |
| Repeats / unacknowledged alerts, incident grouping | `Escalation`, `IncidentCorrelator` | all |

## Measured on this machine (CPU only, 8 cores, Intel iGPU unused)

Concurrent streams over `car-detection.mp4` at target 5 fps per stream (`data/bench/concurrency.md`):

| model | 1 stream | 2 streams | 4 streams |
|---|---|---|---|
| yolox_nano (416px) | 40.9 fps | 25.5 fps each | 10.0 fps each |
| yolox_tiny (416px) | 23.5 fps | 12.7 fps each | 4.3 fps each (below target) |
| yolox_s (640px) | 5.8 fps | 3.0 fps each (below target) | 1.1 fps each (below target) |

Throughput is not accuracy. On the sample video `yolox_tiny` missed a car that `yolox_s` found (0.89), and `yolox_nano`
reported a "toilet". Small models need validation on your own footage (precision/recall by scenario, PDF section 13)
before they are used for anything.

## What is and is not verified

- Verified: unit and scenario tests, real YOLOX inference on public sample videos, the full run path
  (video file -> detector -> tracker -> events -> ingest -> store) and the signed webhook over local HTTP.
- **Not verified against real hardware:** ISAPI / Dahua / ONVIF parsing (written from vendor documentation, tested
  against mock servers), Frigate topics, MQTT broker, Modbus devices, RTSP streams, multicast ONVIF discovery.
- Not implemented: Hanwha SUNAPI, Modbus RTU, BACnet, OPC UA, an actuator/response path, the reporting agent, the
  dashboard. The tracker is a per-label IoU tracker (no motion model); it can swap ids through heavy occlusion.
- Licence: models used here are Apache-2.0 (YOLOX). Ultralytics YOLOv8 weights are AGPL-3.0 and are not used.
