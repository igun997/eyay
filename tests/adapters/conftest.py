"""Shared pytest configuration for adapter tests.

Tests marked ``broker`` need a real MQTT broker. They are skipped unless
``EYEAI_BROKER_TESTS=1`` is set in the environment.
"""

from __future__ import annotations

import os

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "broker: needs a running MQTT broker (set EYEAI_BROKER_TESTS=1)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("EYEAI_BROKER_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="broker test; set EYEAI_BROKER_TESTS=1 to run")
    for item in items:
        if "broker" in item.keywords:
            item.add_marker(skip)
