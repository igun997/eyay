import base64
from datetime import datetime, timezone

import cv2
import numpy as np
import pytest

from eyeai_worker.behavior import Flag, Rule, RuleCache, apply_flags, indication_flags
from eyeai_worker.classifier import (
    RULE_CROP_MARGIN, STATE, ClassifierConfig, ClassifierService, DecisionClient, crop_for_classification,
)
from fakes import FakeClassifierRepo, FakeRepo
from stub_decision_server import GOOD, StubServer

NOW = datetime(2026, 10, 31, 22, 0, tzinfo=timezone.utc)
BBOX = {"x": 0.3, "y": 0.3, "w": 0.2, "h": 0.4}


def rule(key, *, sev="HIGH", thr=0.75, applies=("person",), opens=True, kind="classifier", q=None, config=None, title=None):
    return Rule(key=key, title=title or key.capitalize(), kind=kind, question=q or f"Is it {key}?", applies_to=tuple(applies),
                threshold=thr, config=config or {}, severity=sev, opens_incident=opens)


WEAPON = rule("weapon", sev="CRITICAL", thr=0.70, title="Weapon-like object")
CONCEAL = rule("concealment", sev="HIGH")
MASK = rule("face_covered", sev="MEDIUM")


def jpeg(w=640, h=360):
    return cv2.imencode(".jpg", np.full((h, w, 3), (50, 90, 130), np.uint8))[1].tobytes()


def service(url, repo, **kw):
    return ClassifierService(repo, ClassifierConfig(url=url, poll_s=0.01, **kw))


def queue(rules, **ev):
    repo = FakeClassifierRepo(rules=list(rules))
    repo.add("e1", jpeg(), BBOX, **ev)
    return repo


# ---- rule cache ----------------------------------------------------------------------------------------------------
def test_rule_cache_reloads_only_after_ttl():
    repo = FakeClassifierRepo(rules=[WEAPON])
    now = [0.0]
    cache = RuleCache(repo, ttl_s=30, clock=lambda: now[0])
    assert cache.get() == [WEAPON] and cache.get() == [WEAPON]
    assert repo.rule_loads == 1
    repo.rules = [WEAPON, MASK]
    now[0] = 29.9
    assert len(cache.get()) == 1 and repo.rule_loads == 1
    now[0] = 30.1
    assert len(cache.get()) == 2 and repo.rule_loads == 2


def test_rule_cache_keeps_last_good_list_when_the_db_fails():
    repo = FakeClassifierRepo(rules=[WEAPON])
    now = [0.0]
    cache = RuleCache(repo, ttl_s=1, clock=lambda: now[0])
    cache.get()
    repo.list_behavior_rules = lambda: (_ for _ in ()).throw(RuntimeError("db down"))
    now[0] = 5
    assert cache.get() == [WEAPON]


# ---- requests ------------------------------------------------------------------------------------------------------
def test_one_question_per_request_by_default_with_the_exact_state_wording():
    repo = queue([WEAPON, CONCEAL, MASK])
    with StubServer(noul={"weapon": 0.1}) as s:
        service(s.url, repo).run_once()
    reqs = [r["body"] for r in s.requests]
    assert len(reqs) == 4                                                   # 1 class + 3 rules, never 2+ questions together
    assert set(reqs[0]["questions"]) == {"class"}
    for body, r in zip(reqs[1:], [WEAPON, CONCEAL, MASK]):
        assert body["questions"] == {r.key: {"type": "noul", "instructions": r.question}}
        assert body["state"] == STATE == "Snapshot from a security camera, cropped around a possible detection."
        assert body["model"] == "d1" and len(body["images"]) == 1
    assert repo.events["e1"]["pipeline"] == "ready" and len(repo.events["e1"]["flags"]) == 3


def test_batch_flag_packs_all_questions_in_one_request():
    repo = queue([WEAPON, CONCEAL, MASK])
    with StubServer(noul={"concealment": 0.9}) as s:
        service(s.url, repo, batch=True).run_once()
    assert len(s.requests) == 2
    assert set(s.requests[1]["body"]["questions"]) == {"weapon", "concealment", "face_covered"}
    flags = {f.rule: f for f in repo.events["e1"]["flags"]}
    assert flags["concealment"].triggered and not flags["weapon"].triggered


def test_batch_mode_against_a_server_that_crashes_on_multiple_questions_keeps_the_class_result():
    repo = queue([WEAPON, CONCEAL])
    with StubServer(crash_multi=True) as s:
        service(s.url, repo, batch=True).run_once()
    e = repo.events["e1"]
    assert e["pipeline"] == "ready" and e["cls_label"] == "person"
    assert [f.prob for f in e["flags"]] == [None, None] and not any(f.triggered for f in e["flags"])
    assert e["cls_error"].startswith("behaviour rules unavailable")


def test_default_mode_works_against_a_server_that_crashes_on_multiple_questions():
    repo = queue([WEAPON, CONCEAL])
    with StubServer(crash_multi=True, noul={"weapon": 0.95}) as s:
        service(s.url, repo).run_once()
    e = repo.events["e1"]
    assert e["cls_error"] is None and [f.triggered for f in e["flags"]] == [True, False]


# ---- evaluation ----------------------------------------------------------------------------------------------------
def test_trigger_and_no_trigger_and_flag_shape():
    repo = queue([WEAPON, CONCEAL])
    with StubServer(noul={"weapon": 0.82, "concealment": 0.2}) as s:
        service(s.url, repo).run_once()
    w, c = repo.events["e1"]["flags"]
    assert w.to_json() == {"rule": "weapon", "title": "Weapon-like object", "prob": 0.82, "threshold": 0.70,
                           "severity": "CRITICAL", "triggered": True}
    assert c.to_json() == {"rule": "concealment", "title": "Concealment", "prob": 0.2, "threshold": 0.75, "severity": "HIGH", "triggered": False}
    assert w.detail == "P(yes) 0.82 >= 0.70 - model estimate, needs human review" and c.detail is None


def test_threshold_is_inclusive():
    repo = queue([CONCEAL])
    with StubServer(noul={"concealment": 0.75}) as s:
        service(s.url, repo).run_once()
    assert repo.events["e1"]["flags"][0].triggered


def test_applies_to_filter_and_rule_kind():
    car_only = rule("plate_obscured", applies=("car", "truck"))
    indication = rule("lingering", kind="indication", q=None, config={"indication_kind": "lingering", "min_burst_ms": 5000})
    repo = queue([car_only, indication, MASK])
    with StubServer() as s:
        service(s.url, repo).run_once()
    asked = [k for r in s.requests[1:] for k in r["body"]["questions"]]
    assert asked == ["face_covered"]                                         # no car rule for a person, no model call for indications
    repo2 = queue([car_only], label="car")
    with StubServer() as s2:
        service(s2.url, repo2).run_once()
    assert [k for r in s2.requests[1:] for k in r["body"]["questions"]] == ["plate_obscured"]


def test_false_alarm_skips_the_rules():
    repo = queue([WEAPON, CONCEAL])
    fa = {"answers": {"class": {"type": "choice", "choice": "false_alarm",
                                "probabilities": {"person": 0.1, "vehicle": 0.05, "false_alarm": 0.85}, "confidence": 0.7}}}
    with StubServer(body=fa) as s:
        service(s.url, repo).run_once()
    assert len(s.requests) == 1 and repo.events["e1"]["flags"] == [] and repo.events["e1"]["cls_label"] == "false_alarm"


def test_rules_disabled_by_env_flag():
    repo = queue([WEAPON])
    with StubServer() as s:
        service(s.url, repo, rules_enabled=False).run_once()
    assert len(s.requests) == 1 and repo.events["e1"]["flags"] == []
    assert ClassifierConfig.from_env({"CLASSIFIER_RULES": "0"}).rules_enabled is False
    assert ClassifierConfig.from_env({}).rules_enabled is True


def test_one_failing_rule_request_does_not_lose_the_class_result_or_the_other_rules():
    repo = queue([WEAPON, CONCEAL, MASK])
    with StubServer(fail_keys={"concealment"}, noul={"weapon": 0.9}) as s:
        service(s.url, repo).run_once()
    e = repo.events["e1"]
    assert e["pipeline"] == "ready" and e["cls_label"] == "person" and e["cls_error"] is None   # not everything failed
    probs = {f.rule: f.prob for f in e["flags"]}
    assert probs["concealment"] is None and probs["weapon"] == 0.9 and probs["face_covered"] == 0.05
    assert {f.rule: f.triggered for f in e["flags"]} == {"weapon": True, "concealment": False, "face_covered": False}


def test_rule_frames_evaluates_the_first_n_snapshots_and_takes_the_max():
    repo = FakeClassifierRepo(rules=[WEAPON])
    repo.add("e1", jpeg(), BBOX, snapshots=[(jpeg(), BBOX), (jpeg(), BBOX), (jpeg(), BBOX)])
    with StubServer(noul={"weapon": [0.2, 0.9, 0.1]}) as s:
        service(s.url, repo, rule_frames=2).run_once()
    assert len(s.requests) == 1 + 2                                          # class once, rule on 2 of the 3 snapshots
    assert repo.events["e1"]["flags"][0].prob == 0.9
    repo2 = FakeClassifierRepo(rules=[WEAPON])
    repo2.add("e1", jpeg(), BBOX, snapshots=[(jpeg(), BBOX), (jpeg(), BBOX)])
    with StubServer() as s2:
        service(s2.url, repo2).run_once()                                    # default: first snapshot only
    assert len(s2.requests) == 2


def test_rules_use_a_50_percent_context_crop_while_the_class_question_keeps_15_percent():
    repo = queue([WEAPON])
    with StubServer() as s:
        service(s.url, repo).run_once()

    def dims(req):
        img = cv2.imdecode(np.frombuffer(base64.b64decode(req["body"]["images"][0]), np.uint8), 1)
        return img.shape[1], img.shape[0]

    cw, ch = dims(s.requests[0])
    rw, rh = dims(s.requests[1])
    assert abs(cw - (128 + 2 * 0.15 * 144)) <= 2 and abs(ch - (144 + 2 * 0.15 * 144)) <= 2        # bbox 128x144 px, +15%
    assert abs(rw - (128 + 2 * 0.5 * 144)) <= 2 and abs(rh - (144 + 2 * 0.5 * 144)) <= 2        # +50%
    assert RULE_CROP_MARGIN == 0.5


def test_context_crop_clamps_and_missing_box_means_full_frame():
    img = jpeg()
    bbox = {"x": 270 / 640, "y": 80 / 360, "w": 100 / 640, "h": 200 / 360}
    out = cv2.imdecode(np.frombuffer(crop_for_classification(img, bbox, margin=0.5), np.uint8), 1)
    assert out.shape[1] == 300 and out.shape[0] == 360                       # height clamped to the frame
    full = cv2.imdecode(np.frombuffer(crop_for_classification(img, None, margin=0.5), np.uint8), 1)
    assert full.shape[:2] == (360, 640)


def test_rules_skipped_when_the_queue_is_deeper_than_the_limit():
    repo = FakeClassifierRepo(rules=[WEAPON])
    for i in range(13):
        repo.add(f"e{i}", jpeg(), BBOX)
    with StubServer() as s:
        sv = service(s.url, repo, rule_max_queue=10)
        sv.run_once()                                                        # 12 still queued -> skipped
        assert len(s.requests) == 1 and repo.events["e0"]["flags"] == [] and repo.events["e0"]["pipeline"] == "ready"
        for _ in range(12):
            sv.run_once()
    assert repo.events["e12"]["flags"] and repo.events["e12"]["flags"][0].rule == "weapon"   # queue drained: rules back on
    assert ClassifierConfig.from_env({}).rule_max_queue == 10
    assert ClassifierConfig.from_env({"CLASSIFIER_RULE_MAX_QUEUE": "3"}).rule_max_queue == 3


def test_cls_ms_covers_class_and_rules():
    repo = queue([WEAPON, CONCEAL])
    with StubServer(delay_s=0.05) as s:
        service(s.url, repo).run_once()
    assert repo.events["e1"]["cls_ms"] >= 140                                # 3 requests x 50 ms


def test_config_env_for_rule_options():
    c = ClassifierConfig.from_env({"CLASSIFIER_BATCH": "1", "CLASSIFIER_RULE_FRAMES": "3"})
    assert c.batch is True and c.rule_frames == 3
    d = ClassifierConfig.from_env({})
    assert d.batch is False and d.rule_frames == 1 and d.rules_enabled


def test_rule_request_body_matches_spec():
    body = DecisionClient(ClassifierConfig(url="http://x")).rule_request_body(b"jj", {"weapon": "Is the person holding a weapon?"})
    assert body == {"model": "d1", "state": STATE, "images": [base64.b64encode(b"jj").decode()],
                    "questions": {"weapon": {"type": "noul", "instructions": "Is the person holding a weapon?"}}}


# ---- apply_flags: severity, incident, merge ordering -------------------------------------------------------------------
def make_event(repo, *, severity="HIGH", incident_sev: str | None = "HIGH", label="person"):
    with repo.transaction() as tx:
        inc = None
        if incident_sev:
            inc, _ = tx.create_incident(title="Person detected at Dock 2", severity=incident_sev, source_id="s1", label=label, opened_at=NOW)
        eid = tx.insert_event(source_id="s1", incident_id=inc, label=label, confidence=0.9, severity=severity, track_id=1,
                              bbox=BBOX, title="t", detected_at=NOW)
    return eid, inc


def flag(rule_="weapon", *, sev="CRITICAL", prob=0.82, thr=0.75, trig=True, opens=True, title="Weapon-like object"):
    return Flag(rule=rule_, title=title, prob=prob, threshold=thr, severity=sev, triggered=trig, opens_incident=opens,
                detail=f"P(yes) {prob:.2f} >= {thr:.2f} - model estimate, needs human review" if trig else None)


def apply(repo, eid, *flags):
    with repo.transaction() as tx:
        apply_flags(tx, eid, list(flags), NOW)


def test_trigger_escalates_event_and_incident_and_writes_the_indicator_timeline_entry():
    repo = FakeRepo()
    eid, inc = make_event(repo, severity="MEDIUM", incident_sev="MEDIUM")
    apply(repo, eid, flag())
    assert repo.ev[eid]["suspicious"] and repo.ev[eid]["severity"] == "CRITICAL"
    assert repo.incidents[0]["severity"] == "CRITICAL"
    (t,) = [t for t in repo.timeline if t["kind"] == "system"]
    assert t["title"] == "Behaviour indicator: Weapon-like object"
    assert t["detail"] == "P(yes) 0.82 >= 0.75 - model estimate, needs human review"
    assert "fraud" not in (t["title"] + t["detail"]).lower()
    assert t["incident_id"] == inc and t["event_id"] == eid


def test_severity_is_never_lowered():
    repo = FakeRepo()
    eid, _ = make_event(repo, severity="HIGH", incident_sev="CRITICAL")
    apply(repo, eid, flag("face_covered", sev="MEDIUM", title="Face covered"))
    assert repo.ev[eid]["severity"] == "HIGH" and repo.incidents[0]["severity"] == "CRITICAL" and repo.ev[eid]["suspicious"]


def test_highest_triggered_severity_wins_among_several():
    repo = FakeRepo()
    eid, _ = make_event(repo, severity="LOW", incident_sev="LOW")
    apply(repo, eid, flag("a", sev="MEDIUM", title="A"), flag("b", sev="HIGH", title="B"), flag("c", sev="LOW", title="C", trig=False))
    assert repo.ev[eid]["severity"] == "HIGH" and repo.incidents[0]["severity"] == "HIGH"
    assert [t["title"] for t in repo.timeline if t["kind"] == "system"] == ["Behaviour indicator: A", "Behaviour indicator: B"]


def test_not_triggered_flags_are_stored_without_side_effects():
    repo = FakeRepo()
    eid, _ = make_event(repo)
    apply(repo, eid, flag(trig=False, prob=0.2))
    assert not repo.ev[eid]["suspicious"] and repo.ev[eid]["severity"] == "HIGH" and repo.timeline == []
    assert repo.ev[eid]["flags"][0]["prob"] == 0.2


def test_trigger_opens_an_incident_when_the_event_has_none():
    repo = FakeRepo()
    eid, _ = make_event(repo, severity="LOW", incident_sev=None, label="person")
    assert repo.incidents == []
    apply(repo, eid, flag(sev="CRITICAL"))
    (inc,) = repo.incidents
    assert inc["title"] == "Suspicious: Weapon-like object at Dock 2" and inc["severity"] == "CRITICAL" and inc["label"] == "person"
    assert repo.ev[eid]["incident_id"] == inc["id"] and repo.ev[eid]["severity"] == "CRITICAL"
    (t,) = repo.timeline
    assert t["incident_id"] == inc["id"] and t["title"] == "Behaviour indicator: Weapon-like object"


def test_no_incident_opened_when_the_rule_does_not_open_incidents():
    repo = FakeRepo()
    eid, _ = make_event(repo, incident_sev=None)
    apply(repo, eid, flag(opens=False, sev="LOW", title="Lingering"))
    assert repo.incidents == [] and repo.ev[eid]["suspicious"] and repo.timeline == []


def test_event_status_is_never_touched():
    repo = FakeRepo()
    eid, _ = make_event(repo)
    apply(repo, eid, flag())
    assert "status" not in repo.ev[eid]


@pytest.mark.parametrize("indication_first", [True, False])
def test_merge_keeps_flags_from_both_sources_in_either_order(indication_first):
    repo = FakeRepo()
    eid, _ = make_event(repo, severity="LOW", incident_sev="LOW")
    ind = Flag(rule="lingering", title="Lingering in view", prob=None, threshold=0.75, severity="LOW", triggered=True,
               opens_incident=False, detail="Person lingering in view for 6.0 s (indicator needs 5.0 s or more) - observed in snapshots, needs human review")
    cls = flag("weapon", sev="CRITICAL")
    for f in ([ind, cls] if indication_first else [cls, ind]):
        apply(repo, eid, f)
    assert {f["rule"] for f in repo.ev[eid]["flags"]} == {"lingering", "weapon"}
    assert repo.ev[eid]["severity"] == "CRITICAL" and repo.incidents[0]["severity"] == "CRITICAL"
    assert sorted(t["title"] for t in repo.timeline) == ["Behaviour indicator: Lingering in view", "Behaviour indicator: Weapon-like object"]


def test_reapplying_the_same_rule_replaces_its_flag_and_does_not_duplicate_the_timeline():
    repo = FakeRepo()
    eid, _ = make_event(repo)
    apply(repo, eid, flag(prob=0.8))
    apply(repo, eid, flag(prob=0.9))
    assert [f["prob"] for f in repo.ev[eid]["flags"]] == [0.9] and len(repo.timeline) == 1


def test_indication_flags_only_for_indication_rules_and_matching_label():
    ind = rule("lingering", kind="indication", q=None, applies=("person",), config={"indication_kind": "lingering", "min_burst_ms": 5000})
    flags = indication_flags([WEAPON, ind], "person", "lingering", 6000, "Person lingering in view for 6.0 s")
    assert [f.rule for f in flags] == ["lingering"] and flags[0].triggered and flags[0].prob is None
    assert not indication_flags([ind], "person", "lingering", 4999, "x")[0].triggered
    assert not indication_flags([ind], "person", "moving", 9000, "x")[0].triggered
    assert indication_flags([ind], "car", "lingering", 9000, "x") == []


def test_end_to_end_service_flags_then_burst_flags_merge_in_the_same_event():
    """Classifier writes weapon flag, then the burst's indication flag is merged without losing it."""
    repo = FakeRepo()
    eid, inc = make_event(repo, severity="HIGH", incident_sev="HIGH")
    crepo = FakeClassifierRepo(rules=[WEAPON])
    crepo.add(eid, jpeg(), BBOX)
    # share the fake store: the classifier's flags go through the same apply_flags path as the real repo
    orig = crepo.store_classification

    def store(event_id, result, flags=(), error=None):
        orig(event_id, result, flags, error)
        apply(repo, event_id, *flags)

    crepo.store_classification = store
    with StubServer(noul={"weapon": 0.9}) as s:
        service(s.url, crepo).run_once()
    ind = Flag(rule="lingering", title="Lingering in view", prob=None, threshold=0.75, severity="LOW", triggered=False, opens_incident=False)
    apply(repo, eid, ind)
    assert {f["rule"]: f["triggered"] for f in repo.ev[eid]["flags"]} == {"weapon": True, "lingering": False}
    assert repo.incidents[0]["severity"] == "CRITICAL"
