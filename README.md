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
