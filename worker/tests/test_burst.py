from datetime import datetime, timedelta, timezone

import pytest

from eyeai_worker.behavior import Rule
from eyeai_worker.burst import BurstManager, classify_burst
from eyeai_worker.decision import EventDecider
from eyeai_worker.models import Category

from fakes import FakeRepo, cats, source, td

T0 = datetime(2026, 10, 31, 22, 0, 0, tzinfo=timezone.utc)
BOX = (0.40, 0.30, 0.50, 0.60)          # x1y1x2y2, centre (0.45, 0.45)


def at(s: float) -> datetime:
    return T0 + timedelta(seconds=s)


def category(label="person", *, s=6, frames=4, opens=True, severity="HIGH") -> dict:
    title = label.capitalize()
    return cats(**{label: Category(label, title, severity, opens, 30, True, burst_s=s, burst_frames=frames)})


LINGER_RULE = Rule(key="lingering", title="Lingering in view", kind="indication", question=None, applies_to=("person",),
                   threshold=0.75, config={"indication_kind": "lingering", "min_burst_ms": 5000}, severity="LOW",
                   opens_incident=False)


def start(repo=None, categories=None, *, rules=(), max_pending=20):
    """An event for a person at T0 plus a BurstManager already tracking it."""
    repo = repo or FakeRepo()
    categories = categories or category()
    dec = EventDecider(repo, source(), lambda: categories)
    first = [td("person", 1, bbox=BOX)]
    dec.process(first, lambda: (b"j", 640, 360), at(-0.2))      # min_hits=2: second frame writes the event
    recs = dec.process(first, lambda: (b"j", 640, 360), T0)
    assert len(recs) == 1
    bm = BurstManager(repo, source(), lambda: categories, lambda: list(rules), max_pending=max_pending)
    bm.start(recs[0], first, T0)
    return repo, bm, recs[0]


def frame(*boxes, label="person", track=1):
    return [td(label, track + i, bbox=b) for i, b in enumerate(boxes)]


def play(bm, steps):
    """steps: list of (seconds after T0, tracked list)."""
    for sec, tracked in steps:
        bm.step(tracked, lambda: (b"j", 640, 360), at(sec))


def moved(dx):
    x1, y1, x2, y2 = BOX
    return (x1 + dx, y1, x2 + dx, y2)


# ---- the four kinds ---------------------------------------------------------------------------------------------
def test_moving_burst_stores_four_snapshots_with_own_boxes_and_offsets():
    repo, bm, rec = start()
    play(bm, [(1, frame(moved(0.03))), (2, frame(moved(0.06))), (3, frame(moved(0.09))), (4, frame(moved(0.12))),
              (5, frame(moved(0.15))), (6, frame(moved(0.20)))])
    snaps = [s for s in repo.snaps if s["event_id"] == rec.event_id]
    assert [s["idx"] for s in snaps] == [0, 1, 2, 3]
    assert [s["offset_ms"] for s in snaps] == [0, 2000, 4000, 6000]
    xs = [round(s["bbox"]["x"], 2) for s in snaps]
    assert xs == [0.40, 0.46, 0.52, 0.60] and len(set(xs)) == 4           # a box per snapshot, from the track
    b = repo.bursts[rec.event_id]
    assert b["kind"] == "moving" and b["burst_ms"] == 6000 and b["done"]
    assert b["indication"] == "Person moving across the frame for 6.0 s"
    assert bm.pending == 0


def test_lingering_burst():
    repo, bm, rec = start()
    play(bm, [(2, frame(BOX)), (4, frame(BOX)), (6, frame(BOX))])
    b = repo.bursts[rec.event_id]
    assert b["kind"] == "lingering" and b["indication"] == "Person lingering in view for 6.0 s" and b["burst_ms"] == 6000


def test_crowd_when_three_of_the_label_are_in_the_last_frame():
    cat = category(s=2, frames=2)                                           # too short to be 'lingering' (< 3 s)
    repo, bm, rec = start(categories=cat)
    play(bm, [(2, frame(BOX, moved(0.3), moved(0.5)))])
    b = repo.bursts[rec.event_id]
    assert b["kind"] == "crowd" and b["indication"] == "3 people in view"


def test_appeared_when_the_track_is_gone_after_the_first_snapshot_and_frames_are_still_stored():
    repo, bm, rec = start()
    play(bm, [(2, []), (4, []), (6, [])])
    snaps = [s for s in repo.snaps if s["event_id"] == rec.event_id]
    assert len(snaps) == 4 and snaps[0]["bbox"] is not None and all(s["bbox"] is None for s in snaps[1:])
    b = repo.bursts[rec.event_id]
    assert b["kind"] == "appeared" and b["indication"] == "Person appeared (seen in 1 snapshot)"


def test_other_track_of_the_same_label_does_not_count_as_our_track():
    repo, bm, rec = start()
    play(bm, [(2, frame(moved(0.5), track=2)), (4, frame(moved(0.5), track=2)), (6, frame(moved(0.5), track=2))])
    snaps = [s for s in repo.snaps if s["event_id"] == rec.event_id]
    assert all(s["bbox"] is None for s in snaps[1:]) and repo.bursts[rec.event_id]["kind"] == "appeared"


def test_classify_burst_rules_directly():
    box = {"x": 0.4, "y": 0.3, "w": 0.1, "h": 0.3}
    far = {"x": 0.6, "y": 0.3, "w": 0.1, "h": 0.3}
    assert classify_burst("Person", "person", [box, far], 5100, 1) == ("moving", "Person moving across the frame for 5.1 s")
    assert classify_burst("Person", "person", [box, box], 2999, 1)[0] == "appeared"          # needs >= 3000 ms
    assert classify_burst("Person", "person", [box, box], 3000, 1)[0] == "lingering"
    assert classify_burst("Person", "person", [box, None, box], 4000, 1)[0] == "lingering"   # None frames are skipped
    assert classify_burst("Car", "car", [box], 0, 3) == ("crowd", "3 cars in view")
    assert classify_burst("Bus", "bus", [box], 0, 4)[1] == "4 buses in view"
    assert classify_burst("Person", "person", [box, box, box], 2000, 1) == ("appeared", "Person appeared (seen in 3 snapshots)")
    # just under the displacement threshold is not moving
    edge = {"x": 0.479, "y": 0.3, "w": 0.1, "h": 0.3}
    assert classify_burst("Person", "person", [box, edge], 4000, 1)[0] == "lingering"


# ---- burst_s = 0, caps, stream loss, failures -------------------------------------------------------------------
def test_burst_s_zero_is_a_single_snapshot_and_finishes_immediately():
    repo, bm, rec = start(categories=category(s=0, frames=4))
    assert bm.pending == 0
    assert len([s for s in repo.snaps if s["event_id"] == rec.event_id]) == 1
    b = repo.bursts[rec.event_id]
    assert b["done"] and b["burst_ms"] == 0 and b["kind"] == "appeared" and b["indication"] == "Person appeared (seen in 1 snapshot)"


def test_never_more_snapshots_than_burst_frames_even_with_many_frames():
    repo, bm, rec = start(categories=category(s=6, frames=3))
    play(bm, [(0.5 * i, frame(BOX)) for i in range(1, 41)])                # 20 s of frames at 2 fps
    snaps = [s for s in repo.snaps if s["event_id"] == rec.event_id]
    assert len(snaps) == 3 and [s["offset_ms"] for s in snaps] == [0, 3000, 6000]
    assert bm.pending == 0


def test_slow_frames_still_finish_with_actual_offsets():
    repo, bm, rec = start()
    play(bm, [(2.7, frame(BOX)), (5.9, frame(BOX)), (9.4, frame(BOX))])    # frames arrive late: one sample per frame
    offs = [s["offset_ms"] for s in repo.snaps if s["event_id"] == rec.event_id]
    assert offs == [0, 2700, 5900, 9400] and repo.bursts[rec.event_id]["burst_ms"] == 9400


def test_pending_cap_drops_extra_bursts_gracefully():
    repo = FakeRepo()
    cat = category()
    dec = EventDecider(repo, source(labels=("person", "car")), lambda: cat, min_hits=1)
    bm = BurstManager(repo, source(), lambda: cat, max_pending=2)
    recs = []
    for i in range(3):
        r = dec.process([td("person", 10 + i, bbox=BOX)], lambda: (b"j", 640, 360), at(100 * i))
        recs += r
    for r in recs:
        bm.start(r, [], T0)
    assert bm.pending == 2
    assert recs[2].event_id in repo.bursts and recs[2].event_id not in [p for p in bm._pending]   # third: single snapshot, done


def test_stream_loss_finalizes_with_what_it_has():
    repo, bm, rec = start()
    play(bm, [(2, frame(moved(0.12)))])
    bm.flush(at(3))
    snaps = [s for s in repo.snaps if s["event_id"] == rec.event_id]
    assert len(snaps) == 2 and bm.pending == 0
    b = repo.bursts[rec.event_id]
    assert b["done"] and b["burst_ms"] == 2000 and b["kind"] == "moving"
    bm.flush(at(4))                                                         # idempotent


def test_snapshot_write_failures_do_not_break_the_loop():
    repo, bm, rec = start()
    repo.fail_snapshots = True
    play(bm, [(2, frame(BOX)), (4, frame(BOX)), (6, frame(BOX))])
    assert len([s for s in repo.snaps if s["event_id"] == rec.event_id]) == 1       # only snapshot 0
    assert repo.bursts[rec.event_id]["done"] and bm.pending == 0


def test_event_snapshot_zero_has_bbox_and_zero_offset():
    repo, bm, rec = start()
    s0 = repo.snaps[0]
    assert s0["idx"] == 0 and s0["offset_ms"] == 0 and s0["bbox"] == pytest.approx({"x": 0.4, "y": 0.3, "w": 0.1, "h": 0.3})


# ---- incident timeline + indication rules ------------------------------------------------------------------------
def test_timeline_row_for_events_with_an_incident():
    repo, bm, rec = start()
    play(bm, [(2, frame(BOX)), (4, frame(BOX)), (6, frame(BOX))])
    row = next(t for t in repo.timeline if t["title"].startswith("Indication:"))
    assert row["kind"] == "system" and row["title"] == "Indication: Person lingering in view for 6.0 s"
    assert row["detail"] == "4 snapshots over 6.0 s" and row["incident_id"] == rec.incident_id and row["event_id"] == rec.event_id


def test_no_timeline_row_when_the_event_has_no_incident():
    repo, bm, rec = start(categories=category(opens=False))
    assert rec.incident_id is None
    play(bm, [(2, frame(BOX)), (4, frame(BOX)), (6, frame(BOX))])
    assert not [t for t in repo.timeline if t["title"].startswith("Indication")] and repo.bursts[rec.event_id]["done"]


def test_lingering_indication_rule_triggers_but_never_lowers_severity():
    repo, bm, rec = start(rules=[LINGER_RULE])
    play(bm, [(2, frame(BOX)), (4, frame(BOX)), (6, frame(BOX))])
    ev = repo.ev[rec.event_id]
    assert ev["suspicious"] and ev["severity"] == "HIGH"                    # LOW rule on a HIGH category: not lowered
    assert repo.incidents[0]["severity"] == "HIGH"
    (flag,) = ev["flags"]
    assert flag == {"rule": "lingering", "title": "Lingering in view", "prob": None, "threshold": 0.75, "severity": "LOW", "triggered": True}
    ind = [t for t in repo.timeline if t["title"] == "Behaviour indicator: Lingering in view"]
    assert len(ind) == 1 and ind[0]["kind"] == "system" and "needs human review" in ind[0]["detail"]


def test_indication_rule_recorded_but_not_triggered_when_the_burst_is_too_short_or_other_kind():
    repo, bm, rec = start(categories=category(s=4, frames=3), rules=[LINGER_RULE])      # 4 s < min_burst_ms 5000
    play(bm, [(2, frame(BOX)), (4, frame(BOX))])
    (flag,) = repo.ev[rec.event_id]["flags"]
    assert flag["triggered"] is False and not repo.ev[rec.event_id]["suspicious"]
    repo2, bm2, rec2 = start(rules=[LINGER_RULE])
    play(bm2, [(2, frame(moved(0.1))), (4, frame(moved(0.2))), (6, frame(moved(0.3)))])  # moving, not lingering
    assert repo2.ev[rec2.event_id]["flags"][0]["triggered"] is False


# ---- the tracker hands out a new id when a person moves a lot: follow the nearest box ------------------------------
def test_follows_the_object_when_the_tracker_gives_it_a_new_id():
    repo, bm, rec = start()
    # id 7, 8, 9: new track ids at every sample, each box close (< 0.25) to the previous one, drifting right
    play(bm, [(2, frame(moved(0.05), track=7)), (4, frame(moved(0.10), track=8)), (6, frame(moved(0.15), track=9))])
    snaps = [s for s in repo.snaps if s["event_id"] == rec.event_id]
    assert all(s["bbox"] is not None for s in snaps)
    b = repo.bursts[rec.event_id]
    assert b["kind"] == "moving" and b["indication"] == "Person moving across the frame for 6.0 s"


def test_does_not_jump_to_a_far_away_person_when_the_track_is_lost():
    repo, bm, rec = start()
    play(bm, [(2, frame(moved(0.45), track=7)), (4, frame(moved(0.45), track=8)), (6, frame(moved(0.45), track=9))])   # 0.45 away
    snaps = [s for s in repo.snaps if s["event_id"] == rec.event_id]
    assert all(s["bbox"] is None for s in snaps[1:]) and repo.bursts[rec.event_id]["kind"] == "appeared"


def test_picks_the_nearest_of_several_candidates_and_prefers_the_same_id():
    from eyeai_worker.burst import follow, box_of
    last = box_of(td("person", 1, bbox=BOX))
    near, far = td("person", 5, bbox=moved(0.04)), td("person", 6, bbox=moved(0.2))
    assert follow([far, near], "person", 99, last).track_id == 5          # id gone -> nearest
    assert follow([far, near], "person", 6, last).track_id == 6           # id present -> that one, even if farther
    assert follow([], "person", 1, last) is None and follow([near], "person", 5, None).track_id == 5   # same id wins without a last box
    assert follow([near], "person", 99, None) is None
