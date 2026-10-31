"""Capability discovery before local inference (PDF section 1).

A camera/NVR that already publishes native analytics events for a class
(ISAPI, ONVIF, Frigate, ...) covers that class. The local model is run only
for the labels that no native source covers ("gap labels").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class NativeCapability:
    """What a device reports it already detects natively."""

    device_id: str
    native_labels: frozenset[str]
    source: str  # e.g. "onvif", "isapi", "frigate"

    def __post_init__(self) -> None:
        if not self.device_id:
            raise ValueError("device_id is required")
        if not self.source:
            raise ValueError("source is required")


@dataclass(frozen=True)
class DetectionPlan:
    required_labels: frozenset[str]
    native_labels: frozenset[str]
    gap_labels: frozenset[str]
    attributes: dict[str, str] = field(default_factory=dict)

    @property
    def run_local(self) -> bool:
        return bool(self.gap_labels)


def plan_detection(required: Iterable[str], capability: NativeCapability | None) -> DetectionPlan:
    """Split required labels into native-covered and gap labels."""
    req = frozenset(required)
    if not req:
        raise ValueError("at least one required label is needed")
    native = req & capability.native_labels if capability else frozenset()
    gaps = req - native
    attrs = {"native_source": capability.source} if capability else {}
    return DetectionPlan(required_labels=req, native_labels=native, gap_labels=gaps, attributes=attrs)
