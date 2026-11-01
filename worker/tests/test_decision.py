from datetime import datetime, timedelta, timezone

from eyeai_worker.decision import EventDecider

from fakes import FakeRepo, cats, snap, source, td

T0 = datetime(2026, 10, 31, 2, 0, 0, tzinfo=timezone.utc)


def make(repo=None, src=None, categories=None, **kw):
    repo = repo or FakeRepo()
    categories = categories or cats()
    return repo, EventDecider(repo, src or source(), lambda: categories, **kw)


def feed(dec, dets, t):
    return dec.process(dets, snap, t)


def test_needs_min_hits_before_first_event():
    repo, dec = make()
    assert feed(dec, [td()], T0) == []          # first sighting = flicker
    recs = feed(dec, [td()], T0 + timedelta(seconds=1))
    assert len(recs) == 1 and recs[0].label == "person"
    assert len(repo.events) == 1


def test_event_fields_snapshot_and_title():
    repo, dec = make()
    feed(dec, [td(bbox=(0.1, 0.2, 0.3, 0.6))], T0)
    feed(dec, [td(bbox=(0.1, 0.2, 0.3, 0.6))], T0 + timedelta(seconds=1))
    e = repo.events[0]
    assert e["title"] == "Person detected at Dock 2"
    assert e["severity"] == "HIGH" and e["track_id"] == 1 and e["source_id"] == "s1"
    assert e["bbox"]["x"] == 0.1 and abs(e["bbox"]["w"] - 0.2) < 1e-9 and abs(e["bbox"]["h"] - 0.4) < 1e-9
    assert repo.snapshots == [("e1", 4, 640, 360)]
    assert repo.timeline[0]["kind"] == "camera" and repo.timeline[0]["event_id"] == "e1"


def test_cooldown_blocks_repeat_then_rereports_same_track():
    repo, dec = make()
    for i in range(2):
        feed(dec, [td()], T0 + timedelta(seconds=i))
    assert len(repo.events) == 1
    for s in (5, 20, 29):                        # person cooldown 30 s
        assert feed(dec, [td()], T0 + timedelta(seconds=s)) == []
    assert len(feed(dec, [td()], T0 + timedelta(seconds=32))) == 1   # same track, cooldown elapsed
    assert len(repo.events) == 2


def test_new_track_is_blocked_by_cooldown_then_reported_when_it_elapses():
    repo, dec = make()
    for i in range(2):
        feed(dec, [td(track=1)], T0 + timedelta(seconds=i))
    # track 2 appears at +10 s: within cooldown, nothing yet
    feed(dec, [td(track=1), td(track=2, score=0.5)], T0 + timedelta(seconds=10))
    assert feed(dec, [td(track=1), td(track=2, score=0.5)], T0 + timedelta(seconds=11)) == []
    # cooldown over: the *new* track wins even though track 1 has a higher score
    recs = feed(dec, [td(track=1, score=0.95), td(track=2, score=0.5)], T0 + timedelta(seconds=35))
    assert [r.track_id for r in recs] == [2]


def test_label_filtering_source_labels_category_enabled_and_confidence():
    repo, dec = make(src=source(labels=("person",), min_conf=0.5))
    seq = [td("car"), td("person", score=0.45), td("bus"), td("truck")]
    for i in range(3):
        feed(dec, seq, T0 + timedelta(seconds=i))
    assert repo.events == []                     # car not watched; person below conf; bus/truck not watched
    repo, dec = make(src=source(labels=("truck", "unknown")))
    for i in range(3):
        feed(dec, [td("truck"), td("unknown")], T0 + timedelta(seconds=i))
    assert repo.events == []                     # truck disabled in categories; unknown has no category


def test_labels_have_independent_cooldowns():
    repo, dec = make()
    for i in range(2):
        feed(dec, [td("person", 1), td("car", 2)], T0 + timedelta(seconds=i))
    assert sorted(e["label"] for e in repo.events) == ["car", "person"]


def test_incident_opened_and_grouped_within_window():
    repo, dec = make()
    for i in range(2):
        feed(dec, [td()], T0 + timedelta(seconds=i))
    assert len(repo.incidents) == 1 and repo.incidents[0]["severity"] == "HIGH"
    assert repo.events[0]["incident_id"] == "i1"
    feed(dec, [td(track=2)], T0 + timedelta(seconds=60))
    recs = feed(dec, [td(track=2)], T0 + timedelta(seconds=61))
    assert recs[0].grouped and recs[0].incident_id == "i1"
    assert len(repo.incidents) == 1 and len(repo.timeline) == 2


def test_new_incident_after_group_window():
    repo, dec = make(group_window_s=300)
    for i in range(2):
        feed(dec, [td()], T0 + timedelta(seconds=i))
    for i in range(2):
        feed(dec, [td(track=2)], T0 + timedelta(seconds=400 + i))
    assert len(repo.incidents) == 2
    assert repo.events[1]["incident_id"] == "i2"


def test_resolved_incident_is_not_reused():
    repo, dec = make()
    for i in range(2):
        feed(dec, [td()], T0 + timedelta(seconds=i))
    repo.incidents[0]["status"] = "resolved"
    for i in range(2):
        feed(dec, [td(track=2)], T0 + timedelta(seconds=40 + i))
    assert len(repo.incidents) == 2


def test_category_without_incident_only_writes_event():
    repo, dec = make(src=source(labels=("bus",)))
    for i in range(2):
        feed(dec, [td("bus")], T0 + timedelta(seconds=i))
    assert len(repo.events) == 1 and repo.incidents == [] and repo.timeline == []
    assert repo.events[0]["incident_id"] is None


def test_db_failure_rolls_back_and_retries_next_frame():
    repo, dec = make()
    repo.fail_events = True
    feed(dec, [td()], T0)
    assert feed(dec, [td()], T0 + timedelta(seconds=1)) == []
    assert repo.incidents == [] and repo.events == []     # transaction rolled back, nothing half-written
    repo.fail_events = False
    assert len(feed(dec, [td()], T0 + timedelta(seconds=2))) == 1


def test_snapshot_taken_once_per_frame_and_only_when_needed():
    repo, dec = make()
    calls = []
    def counting():
        calls.append(1)
        return snap()
    dec.process([td("person"), td("car", 2)], counting, T0)
    dec.process([td("person"), td("car", 2)], counting, T0 + timedelta(seconds=1))
    dec.process([td("person"), td("car", 2)], counting, T0 + timedelta(seconds=2))
    assert len(calls) == 1                       # two events on one frame share a snapshot; idle frames take none
