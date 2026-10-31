"""Line-crossing counts aggregated into fixed time buckets per device and label.

Buckets are aligned to the Unix epoch (so hour buckets start on the hour in UTC).
A bucket closes `grace_s` after its end, once the event-time watermark passes that
point; an event arriving for an already-closed bucket is not counted.
"""
from __future__ import annotations

from datetime import datetime, timezone

from eyeai.rules.base import Rule
from eyeai.rules.config import LineCountCfg
from eyeai.schema import Event


class LineCountAggregator(Rule):
    id = "line_count"
    kinds = frozenset({"line_cross"})

    def __init__(self, ctx, cfg: LineCountCfg) -> None:
        super().__init__(ctx, cfg)
        self._counts: dict[tuple[str, str, int], int] = {}

    def _bucket_start(self, ts: datetime) -> int:
        epoch = int(ts.timestamp())
        return epoch - epoch % self.cfg.bucket_s

    def on_event(self, event: Event) -> list[Event]:
        start = self._bucket_start(event.ts_utc)
        if self.ctx.now().timestamp() >= start + self.cfg.bucket_s + self.cfg.grace_s:
            return []
        key = (event.device_id, event.label or "any", start)
        self._counts[key] = self._counts.get(key, 0) + 1
        return []

    def on_tick(self, now: datetime) -> list[Event]:
        out: list[Event] = []
        for key in list(self._counts):
            device, label, start = key
            end = start + self.cfg.bucket_s
            if now.timestamp() < end + self.cfg.grace_s:
                continue
            count = self._counts.pop(key)
            end_ts = datetime.fromtimestamp(end, tz=timezone.utc)
            out.append(
                self.emit(
                    "count_bucket",
                    end_ts,
                    device_id=device,
                    label=label,
                    attributes={
                        "count": count,
                        "bucket_start": datetime.fromtimestamp(start, tz=timezone.utc).isoformat(),
                        "bucket_end": end_ts.isoformat(),
                    },
                    key=key,
                )
            )
        return out
