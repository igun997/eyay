# Engine configuration

The worker (`engine/`) reads two places every poll and merges them. Both are edited from the dashboard (Sources page, admin only).

| Where | Scope | Table |
|---|---|---|
| `engine_settings` row `defaults` | every camera | `engine_settings(key, value jsonb)` |
| `sources.config` | one camera, overrides the defaults leaf by leaf | `sources.config jsonb` |

A key that is absent or `null` inherits. The worker's built-in default (last column below) applies when neither sets it. Invalid
values are ignored (the worker logs a warning and uses the next level), so a bad edit can never stop a stream. The dashboard
validates the same ranges before saving.

Per-camera columns that already exist stay where they are: `sources.labels`, `sources.min_confidence`, `sources.enabled`.

## Schema

```jsonc
{
  "detector": {
    "nms_iou":      0.45,   // 0.1 .. 0.9   same-class boxes above this overlap collapse into one
    "target_fps":   5,      // 0.5 .. 15    analysed frames per second for this camera (env EYEAI_TARGET_FPS when unset)
    "intra_threads": 0      // 0 .. 32      ONNX threads for this camera, 0 = runtime decides (needs a reconnect, env EYEAI_ORT_THREADS when unset)
  },
  "tracker": {
    "iou_threshold": 0.3,   // 0.05 .. 0.9  minimum overlap to keep the same track id
    "max_age":       10,    // 0 .. 60      analysed frames a track survives without a match
    "min_hits":      2      // 1 .. 10      frames a track must be seen before it can become an event
  },
  "events": {
    "new_track_gap_s": 0      // 0 .. 300   0 = off. A track that never produced an event may open one this many seconds after the
                              //            last event of its label even while the category cooldown runs (see "Events and cooldown")
  },
  "calibration": {
    "method":          "none",   // "none" | "line" | "homography"
    "speed_limit_kmh": 50,       // 5 .. 300     faster tracks are flagged "Over speed limit"
    "speed_severity":  "HIGH",   // CRITICAL | HIGH | MEDIUM | LOW, severity of that indicator
    "show_geometry":   true,     // draw the lines / quad on the live view
    // method = "line": two parallel lines across the road and the real distance between them
    "line_axis":  "x",           // "x": lines are vertical, left-right motion is measured; "y": lines are horizontal, up-down motion
    "line1":      0.40,          // 0 .. 1  fraction of the frame width (axis x) or height (axis y); line1 != line2, at least 0.02 apart
    "line2":      0.70,
    "distance_m": 20,            // 0.5 .. 1000  real distance between the two lines
    // method = "homography": four road corners in the image (fractions 0..1, clockwise from bottom-left) and the same corners in metres
    "pixels":    [[0.1, 0.9], [0.1, 0.5], [0.9, 0.5], [0.9, 0.9]],
    "world_m":   [[0, 0], [0, 30], [7, 30], [7, 0]],
    "smoothing": 0.4             // 0 .. 1   higher reacts faster, lower is steadier
  }
}
```

## Language (global only)

`engine_settings 'defaults'` -> `"language": "id" | "en"` (default `id`) sets the language of the text the worker writes: event titles, timeline
entries, activity-pattern sentences and warning-sign details. It applies within one poll and to new text only. Names an admin can edit
(categories, rules, cameras) are not translated by it; migration 008 translates the seeded names once, only where they still hold the original
English text. Questions sent to the optional second AI check stay English (the model's language). The dashboard's own language is a per-user
setting (cookie `eyeai_lang`, default `id`).

## Classifier tuning (global only)

Read from `engine_settings 'defaults'` under `classifier` (a per-camera `classifier` key is ignored: one classifier loop serves all
cameras). The environment variables `CLASSIFIER_*` are the fallback when a key is unset. Applies within 30 s, no restart.

```jsonc
{ "classifier": {
    "rules_enabled": true,  // false: only the person / vehicle / false_alarm class is asked, no behaviour rules
    "rule_frames":   1,     // 1 .. 6    behaviour rules look at the first N snapshots of the burst, the highest probability wins
    "rule_max_queue": 10,   // 0 .. 200  rules are skipped while more events than this wait for the classifier
    "batch":         false  // true: all rule questions in one request (faster; Ollama d1 on CPU crashes with 2+ questions)
} }
```

## Categories

`categories` (title, severity, opens_incident, cooldown_s, enabled, burst_s, burst_frames) is edited from the dashboard too
(Rules page, admin only; `PUT /api/categories/<label>`): title 1..60 chars, severity CRITICAL|HIGH|MEDIUM|LOW, cooldown_s 0..86400,
burst_s 0..60 (0 = single snapshot), burst_frames 1..12. The worker reads categories every poll.

## Events and cooldown

An event is written for a label on a camera at most once per `categories.cooldown_s`, except that a track that has never been
reported may open one after `events.new_track_gap_s` (default 0 = off). Without it a second person who arrives 10 s after the first
is not reported until the 30 s cooldown has passed. Turn it on (5-10 s) where that matters, at the price of more events when the
tracker hands out new ids to the same person.

## Hot reload

Everything applies within one poll (5 s) without reconnecting the stream, except `detector.intra_threads` (rebuilds the
detector session; the stream reconnects). Changing `sources.url`, `kind` or `labels` also reconnects.

## Speed

With `calibration.method` set, every tracked object gets a speed once it has crossed both lines (line) or moved on the road
plane (homography). Time comes from the stream's frame counter divided by its frame rate, never wall-clock. The speed is stored in
`events.speed_kmh` on the event of that track, and a track over `speed_limit_kmh` raises the indicator `speeding` (severity
`speed_severity`) through the same path as the behaviour rules: `events.cls_flags`, `suspicious`, a timeline entry, and an
incident if the event had none. It is an estimate for a person to review, not a verdict: `distance_m` scales every speed linearly,
so a wrong measurement makes every speed wrong by the same factor.
