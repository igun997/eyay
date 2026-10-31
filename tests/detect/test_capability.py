import pytest

from eyeai.detect.capability import DetectionPlan, NativeCapability, plan_detection


def test_no_capability_makes_every_label_a_gap():
    plan = plan_detection(["person", "car"], None)
    assert plan.native_labels == frozenset()
    assert plan.gap_labels == frozenset({"person", "car"})
    assert plan.run_local is True
    assert plan.attributes == {}


def test_native_labels_are_removed_from_gaps():
    cap = NativeCapability(device_id="cam-1", native_labels=frozenset({"person"}), source="onvif")
    plan = plan_detection(["person", "car"], cap)
    assert plan.native_labels == frozenset({"person"})
    assert plan.gap_labels == frozenset({"car"})
    assert plan.run_local is True
    assert plan.attributes == {"native_source": "onvif"}


def test_full_native_coverage_disables_local_inference():
    cap = NativeCapability(device_id="cam-1", native_labels=frozenset({"person", "car"}), source="isapi")
    plan = plan_detection(["person", "car"], cap)
    assert plan.gap_labels == frozenset()
    assert plan.run_local is False


def test_native_labels_outside_requirement_are_ignored():
    cap = NativeCapability(device_id="cam-1", native_labels=frozenset({"truck"}), source="frigate")
    plan = plan_detection(["person"], cap)
    assert plan.native_labels == frozenset()
    assert plan.gap_labels == frozenset({"person"})


def test_required_labels_are_deduplicated():
    plan = plan_detection(["car", "car"], None)
    assert plan.required_labels == frozenset({"car"})


def test_empty_requirement_is_rejected():
    with pytest.raises(ValueError):
        plan_detection([], None)


def test_capability_requires_device_id_and_source():
    with pytest.raises(ValueError):
        NativeCapability(device_id="", native_labels=frozenset(), source="onvif")
    with pytest.raises(ValueError):
        NativeCapability(device_id="cam-1", native_labels=frozenset(), source="")


def test_plan_is_frozen_dataclass():
    plan = DetectionPlan(
        required_labels=frozenset({"car"}),
        native_labels=frozenset(),
        gap_labels=frozenset({"car"}),
    )
    with pytest.raises(Exception):
        plan.gap_labels = frozenset()  # type: ignore[misc]
