"""CLI: `python -m eyeai {run,discover,events}`. (Scenario replay: `python -m eyeai.sim`.)"""
from __future__ import annotations

import argparse
import dataclasses
import json
import signal
import sys
import threading
from datetime import datetime, timedelta, timezone

from .adapters.discovery import discover
from .runner import Engine
from .store import Store


def _run(args: argparse.Namespace) -> int:
    engine = Engine.from_file(args.config)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    engine.start()
    print(f"engine running: {len(engine.adapters)} adapters, {len(engine.videos)} video streams (Ctrl-C to stop)")
    deadline = args.seconds
    try:
        while not stop.wait(args.status_every):
            print(json.dumps(engine.status(), default=str))
            if deadline is not None:
                deadline -= args.status_every
                if deadline <= 0:
                    break
    finally:
        engine.stop()
    return 0


def _discover(args: argparse.Namespace) -> int:
    rep = discover(args.host, args.username, args.password, scheme=args.scheme, port=args.port)
    print(json.dumps(dataclasses.asdict(rep), indent=2, default=str))
    return 0


def _events(args: argparse.Namespace) -> int:
    store = Store(args.db)
    start = datetime.now(timezone.utc) - timedelta(hours=args.hours) if args.hours else None
    for e in store.query_events(site_id=args.site, start=start, limit=args.limit, newest_first=True):
        print(f"{e.ts_utc.isoformat()} {e.provenance.value:11} {e.severity:8} {e.device_id:16} "
              f"{e.kind:22} {e.label or '-':10} {e.zone or '-':12} {json.dumps(e.attributes, default=str)[:90]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="eyeai")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the engine from a YAML config")
    r.add_argument("--config", required=True)
    r.add_argument("--seconds", type=float, default=None, help="stop after N seconds")
    r.add_argument("--status-every", type=float, default=5.0)
    r.set_defaults(fn=_run)
    d = sub.add_parser("discover", help="probe a camera/NVR for native AI capability (ONVIF/ISAPI/Dahua)")
    d.add_argument("host")
    d.add_argument("--username", required=True)
    d.add_argument("--password", required=True)
    d.add_argument("--scheme", default="http")
    d.add_argument("--port", type=int, default=80)
    d.set_defaults(fn=_discover)
    e = sub.add_parser("events", help="print recent stored events")
    e.add_argument("--db", required=True)
    e.add_argument("--site")
    e.add_argument("--hours", type=float, default=None)
    e.add_argument("--limit", type=int, default=50)
    e.set_defaults(fn=_events)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
