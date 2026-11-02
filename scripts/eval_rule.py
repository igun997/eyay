#!/usr/bin/env python3
"""Measure one behaviour rule (a yes/no question to the System One classifier) on your own labelled images.

    scripts/eval_rule.py --url http://localhost:11434 \\
        --question "Is the person holding a knife?" \\
        --positive data/eval/knife/yes --negative data/eval/knife/no

`--positive` holds images where the answer is YES (a person really holding a knife), `--negative` images where it is NO (ordinary
people, tools, phones, umbrellas, kitchen scenes). Use crops the way the worker makes them: the person's box plus 50 % margin
(engine/src/classifier.rs RULE_CROP_MARGIN), or snapshots from your own cameras. Prints, per threshold, how many of the positives
the rule would catch (recall) and how many negatives it would wrongly flag (false-positive rate), and the highest threshold that
still reaches `--target-recall`. Standard library only.

No result here says anything about footage that is not like your test images: collect them from the cameras you will deploy on.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
STATE = "Snapshot from a security camera, cropped around a possible detection."   # same wording as the worker
USER_AGENT = "eyeai-worker/1.0"


def p_yes(url: str, model: str, question: str, image: bytes, timeout: float) -> float:
    body = {
        "model": model, "state": STATE, "images": [base64.b64encode(image).decode()],
        "questions": {"q": {"type": "noul", "instructions": question}},
    }
    req = urllib.request.Request(
        f"{url.rstrip('/')}/v1/systemone", data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return float(json.loads(r.read())["answers"]["q"]["noul"])


def images(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES)


def run(url: str, model: str, question: str, folder: Path, timeout: float) -> list[tuple[Path, float | None, float]]:
    out = []
    for p in images(folder):
        t0 = time.monotonic()
        try:
            prob: float | None = p_yes(url, model, question, p.read_bytes(), timeout)
        except (urllib.error.URLError, OSError, KeyError, ValueError) as e:
            print(f"  ! {p.name}: {e}", file=sys.stderr)
            prob = None
        out.append((p, prob, time.monotonic() - t0))
    return out


def report(pos: list[float], neg: list[float], target_recall: float) -> None:
    print(f"\npositives: {len(pos)}   negatives: {len(neg)}")
    if not pos or not neg:
        print("need both positives and negatives for a meaningful report")
        return
    print(f"{'threshold':>9}  {'recall':>14}  {'false positives':>17}")
    best = None
    for t in [0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.75, 0.8, 0.9, 0.95]:
        tp = sum(p >= t for p in pos)
        fp = sum(p >= t for p in neg)
        print(f"{t:>9.2f}  {tp:>3}/{len(pos):<3} {tp / len(pos):>5.0%}  {fp:>3}/{len(neg):<3} {fp / len(neg):>5.0%}")
        if tp / len(pos) >= target_recall:
            best = t
    if best is None:
        print(f"\nno threshold reaches {target_recall:.0%} recall: the question or the images need work, not the threshold")
    else:
        print(f"\nhighest threshold with recall >= {target_recall:.0%}: {best:.2f}")
    print("median P(yes): positives %.2f, negatives %.2f" % (sorted(pos)[len(pos) // 2], sorted(neg)[len(neg) // 2]))
    print("(a rule is an indicator for a person to review; judge it by how many misses you can accept, not by accuracy alone)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True, help="classifier base URL (Ollama serving the model), e.g. http://localhost:11434")
    ap.add_argument("--model", default="d1")
    ap.add_argument("--question", required=True, help="the yes/no question, as stored in behavior_rules.question")
    ap.add_argument("--positive", type=Path, required=True)
    ap.add_argument("--negative", type=Path, required=True)
    ap.add_argument("--target-recall", type=float, default=0.9)
    ap.add_argument("--timeout", type=float, default=120)
    ap.add_argument("--csv", type=Path, help="also write every image and its P(yes) here")
    a = ap.parse_args()
    for folder in (a.positive, a.negative):
        if not folder.is_dir():
            ap.error(f"{folder} is not a folder")
    print(f"question: {a.question}")
    rows = []
    for label, folder in (("yes", a.positive), ("no", a.negative)):
        print(f"{label}: {len(images(folder))} images from {folder}")
        rows += [(label, *r) for r in run(a.url, a.model, a.question, folder, a.timeout)]
    ok = [(label, prob) for label, _, prob, _ in rows if prob is not None]
    failed = len(rows) - len(ok)
    if failed:
        print(f"\n{failed} images could not be scored and are left out")
    report([p for l, p in ok if l == "yes"], [p for l, p in ok if l == "no"], a.target_recall)
    ms = [t * 1000 for *_, t in rows]
    if ms:
        print(f"latency per image: median {sorted(ms)[len(ms) // 2]:.0f} ms, max {max(ms):.0f} ms")
    if a.csv:
        a.csv.write_text("label,file,p_yes,seconds\n" + "".join(f"{l},{p},{'' if pr is None else f'{pr:.4f}'},{t:.2f}\n" for l, p, pr, t in rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
