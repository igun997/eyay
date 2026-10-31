"""Engine runner: wires adapters + local video pipelines into Ingest and the Store.

Engine config (YAML)::

    site_id: site-1
    store: data/eyeai.db            # sqlite path (default :memory:)
    rules: config/rules.example.yaml
    tick_s: 1.0                     # how often time-based rules are advanced
    adapters: {...}                 # same schema as eyeai.adapters.registry (site_id is injected)
    video:                          # local inference, only for labels native sources do not cover
      - device_id: cam-gate-01
        source: rtsp://... | path/to/file.mp4
        model: data/models/yolox_s.onnx
        labels: [car, person]
        native_labels: [person]     # labels a native source already covers (from `discover`)
        native_source: isapi
        target_fps: 5
        conf: 0.3
        zones:  [{name: gate, polygon: [[0,0.3],[1,0.3],[1,1],[0,1]]}]
        lines:  [{name: mid, p1: [0,0.6], p2: [1,0.6]}]

The engine only produces events. It has no actuator output of any kind.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import yaml

from .adapters.registry import build_adapters
from .detect.capability import NativeCapability, plan_detection
from .detect.lines import Line
from .detect.stream import VideoPipeline
from .detect.yolox import YoloxDetector
from .detect.zones import Zone
from .ingest import Ingest
from .rules.config import RulesConfig, load_config, parse_config
from .schema import Event, utcnow
from .store import Store

_TOP = {"site_id", "store", "rules", "tick_s", "adapters", "video"}
_VIDEO_KEYS = {"device_id", "source", "model", "labels", "native_labels", "native_source",
               "target_fps", "conf", "zones", "lines", "min_hits", "realtime", "snapshot_dir"}


def _check_keys(where: str, cfg: dict, allowed: set[str], required: set[str] = frozenset()) -> None:
    unknown = set(cfg) - allowed
    if unknown:
        raise ValueError(f"{where}: unknown keys {sorted(unknown)}")
    missing = required - set(cfg)
    if missing:
        raise ValueError(f"{where}: missing keys {sorted(missing)}")


def _pt(where: str, v: Any) -> tuple[float, float]:
    if not (isinstance(v, (list, tuple)) and len(v) == 2):
        raise ValueError(f"{where}: expected [x, y]")
    return float(v[0]), float(v[1])


class Engine:
    def __init__(self, store: Store, ingest: Ingest, adapters: list[Any], videos: list[VideoPipeline],
                 tick_s: float = 1.0):
        self.store = store
        self.ingest = ingest
        self.adapters = adapters
        self.videos = videos
        self._tick_s = tick_s
        self._stop = threading.Event()
        self._tick_thread: threading.Thread | None = None
        self.errors = 0

    # ---- construction -----------------------------------------------------
    @classmethod
    def from_dict(cls, cfg: dict[str, Any], *, base_dir: Path | None = None,
                  rules_cfg: RulesConfig | None = None) -> "Engine":
        _check_keys("engine config", cfg, _TOP, {"site_id"})
        base = base_dir or Path.cwd()
        site_id = cfg["site_id"]
        store = Store(str(cfg.get("store", ":memory:")))
        if rules_cfg is None:
            if "rules" in cfg:
                rules_path = Path(cfg["rules"])
                rules_cfg = load_config(rules_path if rules_path.is_absolute() else base / rules_path)
            else:
                rules_cfg = parse_config({"site_id": site_id})
        if rules_cfg.site_id != site_id:
            raise ValueError(f"rules site_id {rules_cfg.site_id!r} != engine site_id {site_id!r}")
        ingest = Ingest(store, rules_cfg)
        engine = cls(store, ingest, [], [], float(cfg.get("tick_s", 1.0)))
        emit = engine.emit
        adapter_cfg = {"site_id": site_id, **(cfg.get("adapters") or {})}
        engine.adapters = build_adapters(adapter_cfg, emit)
        engine.videos = [engine._video(site_id, v, emit) for v in (cfg.get("video") or [])]
        return engine

    @classmethod
    def from_file(cls, path: str | Path) -> "Engine":
        p = Path(path)
        return cls.from_dict(yaml.safe_load(p.read_text()), base_dir=p.parent)

    @staticmethod
    def _video(site_id: str, v: dict[str, Any], emit) -> VideoPipeline:
        _check_keys("video[]", v, _VIDEO_KEYS, {"device_id", "source", "model", "labels"})
        native = frozenset(v.get("native_labels") or [])
        cap = NativeCapability(v["device_id"], native, v.get("native_source", "unknown")) if native else None
        plan = plan_detection(v["labels"], cap)
        zones = [Zone(z["name"], tuple(_pt("zone", p) for p in z["polygon"])) for z in v.get("zones") or []]
        lines = [Line(l["name"], _pt("line.p1", l["p1"]), _pt("line.p2", l["p2"])) for l in v.get("lines") or []]
        detector = YoloxDetector(v["model"], conf_thresh=float(v.get("conf", 0.3)), classes=plan.gap_labels)
        return VideoPipeline(
            v["source"], site_id=site_id, device_id=v["device_id"], detector=detector, emit=emit, plan=plan,
            model_name=Path(v["model"]).stem, zones=zones, lines=lines,
            target_fps=float(v.get("target_fps", 5.0)), min_hits=int(v.get("min_hits", 2)),
            realtime=v.get("realtime"), snapshot_dir=v.get("snapshot_dir"),
        )

    # ---- runtime ------------------------------------------------------------
    def emit(self, event: Event) -> None:
        """Entry point for every source. Never raises into the source thread."""
        try:
            self.ingest.handle(event)
        except Exception as exc:
            self.errors += 1
            self.store.audit("engine", "ingest_error", {"error": f"{type(exc).__name__}: {exc}",
                                                         "event_id": event.id, "kind": event.kind})

    def start(self) -> None:
        for a in self.adapters:
            a.start()
        for v in self.videos:
            v.start()
        self._stop.clear()
        self._tick_thread = threading.Thread(target=self._tick_loop, name="engine-tick", daemon=True)
        self._tick_thread.start()

    def stop(self) -> None:
        self._stop.set()
        for v in self.videos:
            v.stop()
        for a in self.adapters:
            a.stop()
        for v in self.videos:
            v.join(5.0)
        if self._tick_thread:
            self._tick_thread.join(5.0)

    def _tick_loop(self) -> None:
        while not self._stop.wait(self._tick_s):
            try:
                self.ingest.tick(utcnow())
            except Exception as exc:
                self.errors += 1
                self.store.audit("engine", "tick_error", {"error": f"{type(exc).__name__}: {exc}"})

    def status(self) -> dict[str, Any]:
        return {
            "adapters": [a.status() for a in self.adapters],
            "video": [v.status() for v in self.videos],
            "ingest": dict(self.ingest.stats),
            "errors": self.errors,
        }
