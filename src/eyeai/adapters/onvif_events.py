"""ONVIF event adapter using the PullPoint subscription (Profile S/T/M).

Protocol (from the ONVIF Core / Events service specification, NOT verified on
hardware here):
  1. tev:CreatePullPointSubscription on the event service URL
     -> wsnt:SubscriptionReference/wsa:Address = pull-point URL.
  2. tev:PullMessages on the pull-point URL (long poll, Timeout / MessageLimit).
  3. wsnt:Renew before InitialTerminationTime lapses, wsnt:Unsubscribe on stop.

Every request carries a WS-Security UsernameToken with PasswordDigest
(Base64(SHA-1(nonce + created + password)), nonce Base64 encoded).

Provenance: NATIVE (analytics ran inside the camera or NVR).
Unknown topics are dropped; no event is invented for them.
"""
from __future__ import annotations

import base64
import hashlib
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

import httpx

from ..schema import Emit, Event, Provenance, utcnow
from .base import ThreadedAdapter

NS = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "tev": "http://www.onvif.org/ver10/events/wsdl",
    "wsnt": "http://docs.oasis-open.org/wsn/b-2",
    "wsa": "http://www.w3.org/2005/08/addressing",
    "tt": "http://www.onvif.org/ver10/schema",
    "wsse": "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd",
    "wsu": "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd",
}

# Topic substring -> (kind, label, severity). First match wins.
TOPIC_MAP: list[tuple[str, str, str | None, str]] = [
    ("LineDetector/Crossed", "line_cross", None, "warning"),
    ("LineDetector", "line_cross", None, "warning"),
    ("FieldDetector/ObjectsInside", "intrusion", None, "warning"),
    ("FieldDetector", "intrusion", None, "warning"),
    ("CellMotionDetector/Motion", "motion", None, "info"),
    ("MotionAlarm", "motion", None, "info"),
    ("TamperDetector/Tamper", "tamper", None, "warning"),
    ("ImageTooDark", "tamper", None, "warning"),
    ("VideoSource/SignalLoss", "video_loss", None, "warning"),
    ("Trigger/DigitalInput", "io_input", "alarm_input", "info"),
    ("Trigger/Relay", "io_input", "relay_state", "info"),
]

_SOAP_ENV = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="{s}" xmlns:tev="{tev}" xmlns:wsnt="{wsnt}" '
    'xmlns:wsa="{wsa}" xmlns:wsse="{wsse}" xmlns:wsu="{wsu}">'
    "<s:Header>{header}</s:Header>"
    "<s:Body>{body}</s:Body></s:Envelope>"
)


def wsse_header(username: str, password: str, *, now: datetime | None = None) -> str:
    """Build a WS-Security UsernameToken header with PasswordDigest."""
    nonce = os.urandom(16)
    created = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    digest = base64.b64encode(
        hashlib.sha1(nonce + created.encode() + password.encode()).digest()
    ).decode()
    return (
        "<wsse:Security s:mustUnderstand=\"1\">"
        "<wsse:UsernameToken>"
        f"<wsse:Username>{_esc(username)}</wsse:Username>"
        "<wsse:Password Type=\"http://docs.oasis-open.org/wss/2004/01/"
        "oasis-200401-wss-username-token-profile-1.0#PasswordDigest\">"
        f"{digest}</wsse:Password>"
        "<wsse:Nonce EncodingType=\"http://docs.oasis-open.org/wss/2004/01/"
        "oasis-200401-wss-soap-message-security-1.0#Base64Binary\">"
        f"{base64.b64encode(nonce).decode()}</wsse:Nonce>"
        f"<wsu:Created>{created}</wsu:Created>"
        "</wsse:UsernameToken></wsse:Security>"
    )


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&apos;")
    )


def build_envelope(header: str, body: str) -> str:
    return _SOAP_ENV.format(header=header, body=body, **{
        "s": NS["s"], "tev": NS["tev"], "wsnt": NS["wsnt"],
        "wsa": NS["wsa"], "wsse": NS["wsse"], "wsu": NS["wsu"],
    })


def parse_xml(body: bytes) -> ET.Element:
    """Parse a SOAP response. DTDs are refused to block entity expansion."""
    if b"<!DOCTYPE" in body or b"<!ENTITY" in body:
        raise ValueError("XML with DOCTYPE/ENTITY rejected")
    return ET.fromstring(body)


def _topic_kind(topic: str) -> tuple[str, str | None, str] | None:
    for needle, kind, label, severity in TOPIC_MAP:
        if needle in topic:
            return kind, label, severity
    return None


def parse_pull_messages(body: bytes) -> list[dict[str, Any]]:
    """Return one dict per NotificationMessage: topic, utc, operation, data."""
    root = parse_xml(body)
    out: list[dict[str, Any]] = []
    for note in root.iter(f"{{{NS['wsnt']}}}NotificationMessage"):
        topic_el = note.find("wsnt:Topic", NS)
        topic = (topic_el.text or "").strip() if topic_el is not None else ""
        msg = note.find("wsnt:Message/tt:Message", NS)
        utc_text = None
        operation = None
        data: dict[str, str] = {}
        if msg is not None:
            utc_text = msg.get("UtcTime")
            operation = msg.get("PropertyOperation")
            for item in msg.findall("tt:Source/tt:SimpleItem", NS):
                data[item.get("Name", "")] = item.get("Value", "")
            for item in msg.findall("tt:Data/tt:SimpleItem", NS):
                data[item.get("Name", "")] = item.get("Value", "")
        out.append({"topic": topic, "utc": utc_text, "operation": operation, "data": data})
    return out


def _parse_utc(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        return None
    return value.astimezone(timezone.utc)


def message_to_event(msg: dict[str, Any], site_id: str, device_id: str) -> Event | None:
    mapping = _topic_kind(msg["topic"])
    if mapping is None:
        return None
    kind, label, severity = mapping
    ts = _parse_utc(msg.get("utc"))
    ts_source = "source" if ts is not None else "arrival"
    attributes: dict[str, Any] = {
        "source_topic": msg["topic"],
        "property_operation": msg.get("operation"),
        "ts_source": ts_source,
    }
    data = msg.get("data") or {}
    state = data.get("State") or data.get("IsMotion") or data.get("Value")
    if state is not None:
        attributes["state_raw"] = state
    if "Rule" in data:
        attributes["rule"] = data["Rule"]
    return Event(
        site_id=site_id,
        device_id=device_id,
        ts_utc=ts or utcnow(),
        kind=kind,
        label=label,
        confidence=1.0,
        provenance=Provenance.NATIVE,
        severity=severity,
        attributes=attributes,
    )


class OnvifEventAdapter(ThreadedAdapter):
    """PullPoint subscriber. Renews, pulls, and unsubscribes on stop."""

    def __init__(
        self,
        site_id: str,
        device_id: str,
        emit: Emit,
        *,
        host: str,
        username: str,
        password: str,
        port: int = 80,
        scheme: str = "http",
        event_url: str | None = None,
        termination: str = "PT60S",
        pull_timeout: str = "PT10S",
        message_limit: int = 100,
        **kwargs: Any,
    ) -> None:
        super().__init__(site_id, device_id, emit, **kwargs)
        self._event_url = event_url or f"{scheme}://{host}:{port}/onvif/event_service"
        self._username = username
        self._password = password
        self._termination = termination
        self._pull_timeout = pull_timeout
        self._message_limit = message_limit
        self._subscription_url: str | None = None

    # -- SOAP helpers -----------------------------------------------------

    def _post(self, url: str, body_xml: str, timeout: float) -> bytes:
        header = wsse_header(self._username, self._password)
        envelope = build_envelope(header, body_xml)
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                url,
                content=envelope.encode("utf-8"),
                headers={"Content-Type": "application/soap+xml; charset=utf-8"},
            )
        if resp.status_code >= 400:
            raise RuntimeError(f"ONVIF HTTP {resp.status_code} from {url}")
        return resp.content

    def _create_subscription(self) -> str:
        body = (
            "<tev:CreatePullPointSubscription>"
            f"<tev:InitialTerminationTime>{self._termination}</tev:InitialTerminationTime>"
            "</tev:CreatePullPointSubscription>"
        )
        root = parse_xml(self._post(self._event_url, body, timeout=15.0))
        addr = root.find(".//wsnt:SubscriptionReference/wsa:Address", NS)
        if addr is None or not (addr.text or "").strip():
            raise RuntimeError("CreatePullPointSubscription returned no subscription address")
        return addr.text.strip()

    def _pull(self, url: str) -> list[dict[str, Any]]:
        body = (
            "<tev:PullMessages>"
            f"<tev:Timeout>{self._pull_timeout}</tev:Timeout>"
            f"<tev:MessageLimit>{self._message_limit}</tev:MessageLimit>"
            "</tev:PullMessages>"
        )
        return parse_pull_messages(self._post(url, body, timeout=30.0))

    def _renew(self, url: str) -> None:
        body = (
            "<wsnt:Renew>"
            f"<wsnt:TerminationTime>{self._termination}</wsnt:TerminationTime>"
            "</wsnt:Renew>"
        )
        self._post(url, body, timeout=15.0)

    def _unsubscribe(self, url: str) -> None:
        self._post(url, "<wsnt:Unsubscribe/>", timeout=5.0)

    # -- ThreadedAdapter hooks -------------------------------------------

    def _session(self) -> None:
        url = self._create_subscription()
        self._subscription_url = url
        self._set_connected(True)
        try:
            renew_every = _seconds(self._termination) * 0.5
            last_renew = time.monotonic()
            while not self._stop_requested:
                for msg in self._pull(url):
                    event = message_to_event(msg, self.site_id, self.device_id)
                    if event is not None:
                        self._emit_event(event)
                if time.monotonic() - last_renew >= renew_every:
                    self._renew(url)
                    last_renew = time.monotonic()
        finally:
            try:
                self._unsubscribe(url)
            except Exception:  # noqa: BLE001 - best effort on teardown
                pass
            self._subscription_url = None


def _seconds(duration: str) -> float:
    """Parse an xsd:duration of the form PT<n>S / PT<n>M only."""
    text = duration.strip()
    if not text.startswith("PT"):
        raise ValueError(f"unsupported duration {duration!r}")
    body = text[2:]
    if body.endswith("S"):
        return float(body[:-1])
    if body.endswith("M"):
        return float(body[:-1]) * 60.0
    raise ValueError(f"unsupported duration {duration!r}")
