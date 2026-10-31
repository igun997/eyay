"""Vibration anomaly per machine (device) using an EWMA baseline.

The first `warmup` samples build the baseline without judging. After that a sample
is anomalous when |z| > `z_threshold`, where the standard deviation is floored at 1% of
the mean. `consecutive` anomalous samples in a row emit one `machine_anomaly`. The
baseline is frozen while samples are anomalous, so a sustained fault does not get
learned as normal. It resumes once readings return to normal.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from eyeai.rules.base import Rule
from eyeai.rules.config import MachineCfg
from eyeai.schema import Event


@dataclass
class _Stat:
    n: int = 0
    mean: float = 0.0
    var: float = 0.0
    streak: int = 0
    alarmed: bool = False

    def update(self, x: float, alpha: float) -> None:
        self.n += 1
        diff = x - self.mean
        incr = alpha * diff
        self.mean += incr
        self.var = (1 - alpha) * (self.var + diff * incr)


class MachineAnomaly(Rule):
    id = "machine_anomaly"
    kinds = frozenset({"vibration"})

    def __init__(self, ctx, cfg: MachineCfg) -> None:
        super().__init__(ctx, cfg)
        self._state: dict[str, _Stat] = {}

    def on_event(self, event: Event) -> list[Event]:
        x = float(event.attributes["value"])
        st = self._state.setdefault(event.device_id, _Stat())
        cfg = self.cfg
        if st.n == 0:
            st.mean, st.var, st.n = x, 0.0, 1
            return []
        if st.n < cfg.warmup:
            st.update(x, cfg.alpha)
            return []
        std = max(math.sqrt(st.var), abs(st.mean) * 0.01, 1e-6)
        z = (x - st.mean) / std
        if abs(z) <= cfg.z_threshold:
            st.streak = 0
            st.alarmed = False
            st.update(x, cfg.alpha)
            return []
        st.streak += 1
        if st.streak < cfg.consecutive or st.alarmed:
            return []
        st.alarmed = True
        return [
            self.emit(
                "machine_anomaly",
                event.ts_utc,
                device_id=event.device_id,
                label="vibration",
                severity="warning",
                attributes={"value": x, "z": round(z, 2), "baseline_mean": round(st.mean, 6)},
                key=(event.device_id,),
            )
        ]
