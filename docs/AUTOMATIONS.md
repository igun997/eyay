# Alerts and automations

"When event A happens, simulate or send message B to a webhook, an MQTT broker or a PLC, in real time." Built in the dashboard
(**Alerts**), executed by the worker the moment the event, indicator or speed arrives (about a second), never by the web app.

## Safety first (read before enabling anything live)

This reverses the earlier "no actuators" stance on purpose, with these limits:

- **Everything starts as simulation.** A new automation is `simulate` and disabled. Simulation renders exactly what would be sent
  and logs it; nothing leaves the building. Going `live` is an explicit admin action with a confirmation.
- **Alerts and indications only.** Use outputs such as a lamp, a siren relay or a message. **Never wire this to a safety function**
  (emergency stop, interlock, door release, machine control). The indicators come from a camera model and can be wrong or late.
- **Coil writes pulse.** A Modbus coil is switched on for `hold_s` (1..60 s) and then off again by the worker; the builder offers no
  latching write. Registers are written once.
- **Limits.** Per-camera cooldown (default 60 s) and a hard cap per hour (default 30) per automation; extra firings are logged as
  `skipped`. Each action has a short timeout; a slow or dead target never delays detection.
- **Everything is logged.** Every firing, test and failure is a row in `automation_runs` and an `audit_log` entry for changes.
- **Admins only** create, edit, enable and test. Security staff can read the activity log.
- Secrets (webhook secret, MQTT password) are stored as entered and are never returned by the API.

## Data model (migration 006)

`connections(id, name, kind webhook|mqtt|modbus, config, enabled)`, `automations(id, name, description, enabled, mode simulate|live,
conditions, actions, cooldown_s, max_per_hour, position)`, `automation_runs(...)`: see `db/migrations/006_automations.sql`.

### Connection config

| kind | config |
|---|---|
| webhook | `{"url": "https://…", "method": "POST"\|"PUT", "headers": {"X-Api-Key": "…"}, "secret": "…", "timeout_s": 5}`; `secret` signs the body: header `X-EyeAI-Signature: sha256=<hex hmac>` |
| mqtt | `{"host": "broker", "port": 1883, "username": "", "password": "", "client_id": "eyeai", "timeout_s": 5}` (MQTT 3.1.1, QoS 0, no TLS yet) |
| modbus | `{"host": "plc", "port": 502, "unit_id": 1, "timeout_s": 5}` (Modbus TCP) |

The API returns secret fields as `"********"` when set; sending `"********"` (or omitting the key) keeps the stored value, `""` clears it.

### Conditions (all must hold; empty list = every event, the UI warns)

```jsonc
{"field": "camera",       "in": ["<source id>", …]}
{"field": "label",        "in": ["person", "car", …]}              // what the camera saw
{"field": "severity",     "at_least": "HIGH"}                       // LOW < MEDIUM < HIGH < CRITICAL
{"field": "indicator",    "triggered": "weapon"}                    // a behaviour rule key, or "*" for any
{"field": "classified_as","is": "person"}                           // person | vehicle | false_alarm (the AI check)
{"field": "speed_kmh",    "above": 60}
{"field": "suspicious",   "is": true}
{"field": "time",         "between": ["22:00", "06:00"], "utc_offset_min": 420}   // may wrap midnight; local time of the site
```

An automation is evaluated every time the event changes (created, classified, indicator, speed) and fires **once per event** the first
time all conditions hold.

### Actions

```jsonc
{"type": "webhook", "connection": "<id>", "body": "optional template"}       // default body below
{"type": "mqtt",    "connection": "<id>", "topic": "plant/alarm", "payload": "template", "retain": false}
{"type": "modbus",  "connection": "<id>", "function": "write_coil",     "address": 0,  "hold_s": 5}
{"type": "modbus",  "connection": "<id>", "function": "write_register", "address": 10, "value": 1}
```

Templates replace `{{name}}`; unknown names become an empty string. Names: `event.id`, `event.code`, `event.label`, `event.severity`,
`event.title`, `event.detected_at`, `event.speed_kmh`, `event.suspicious`, `event.classified_as`, `event.indicators` (titles, comma
separated), `camera.id`, `camera.code`, `camera.name`, `incident.code`, `automation.name`, `mode`.

Default webhook body (and default MQTT payload):

```json
{"eyeai": 1, "mode": "live", "test": false, "sent_at": "2026-11-02T11:00:00Z",
 "automation": {"id": "…", "name": "…"},
 "event": {"id": "…", "code": "evt_ab12cd", "label": "person", "severity": "HIGH", "title": "…", "detected_at": "…",
           "speed_kmh": null, "suspicious": true, "classified_as": "person", "indicators": ["Weapon-like object"]},
 "camera": {"id": "…", "code": "CAM-01", "name": "Dock 2"}, "incident": {"code": "INC-0042"}}
```

## Simulation, previews and tests

The web app never talks to a target. It inserts an `automation_runs` row with `status = 'queued'`; the worker picks it up within
about a second, runs it with a sample event (`test: true`) and updates the row.

| mode | what happens |
|---|---|
| `test_simulate` (automation > Preview) | renders every action's payload / frame, sends nothing; status `simulated` |
| `test_send` (automation > Send test) | really sends the sample to each action target; status `sent` / `failed` |
| connection test (`connection_id` set, `automation_id` NULL) | webhook: POST `{"eyeai":1,"test":true}`; MQTT: connect and check CONNACK; Modbus: TCP connect only (no write) |
| `simulate` (automation mode) | real events; payloads rendered and logged as `simulated`, nothing sent |
| `live` | real events; actions executed, `sent` / `failed` |

`detail.actions[i]` = `{"type","target","ok","message","would_send","ms"}`; `would_send` holds the rendered body, topic or Modbus frame
description, for both simulation and live (so the activity log always shows what went out).

## Web API (admin writes, any signed-in user reads; all writes go to `audit_log`)

- `GET/POST /api/connections`, `PUT/DELETE /api/connections/[id]`, `POST /api/connections/[id]/test`
- `GET/POST /api/automations`, `PUT/DELETE /api/automations/[id]`, `POST /api/automations/[id]/preview`, `POST /api/automations/[id]/test`
- `GET /api/automations/[id]/runs?limit=50` and `GET /api/automation-runs?limit=100` (latest first, with the automation name)

Validation: names 1..80 chars; unknown keys rejected; conditions and actions checked against the shapes above; an action must name an
enabled connection of the matching kind; coil `hold_s` 1..60; `address` 0..65535; register `value` 0..65535; `cooldown_s` 0..86400;
`max_per_hour` 1..1000; switching an automation to `live` requires `"confirm_live": true` in the request body.
