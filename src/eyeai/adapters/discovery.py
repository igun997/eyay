"""Capability discovery for one camera or NVR, run before any local inference.

Each probe is best effort: a failing probe records a reason and never raises.
Endpoints below come from vendor documentation and were NOT verified on hardware:

- Hikvision ISAPI: GET /ISAPI/System/deviceInfo, /ISAPI/Event/triggers,
  /ISAPI/System/IO/inputs (HTTP digest auth).
- Dahua CGI: GET /cgi-bin/magicBox.cgi?action=getDeviceType|getSoftwareVersion,
  and a short-lived GET /cgi-bin/eventManager.cgi?action=attach (digest auth).
  The attach probe reads the response headers only and closes the stream.
- ONVIF device/media services over SOAP with WS-Security UsernameToken
  (GetDeviceInformation, GetCapabilities, media GetProfiles, GetStreamUri).
  Profile S/M/T is a presence heuristic: S = Media service, M = Analytics
  service, T = Media2 service. It is not a conformance test.
- WS-Discovery: multicast Probe to 239.255.255.250:3702 (`ws_discover`).

Hanwha SUNAPI is NOT implemented. A Hanwha/Wisenet ONVIF manufacturer yields
vendor `'unsupported_probe'`; only its ONVIF services are probed.
"""
from __future__ import annotations

import logging
import socket
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from typing import Any
from xml.sax.saxutils import escape

import httpx

from .isapi_events import EVENT_MAP as ISAPI_EVENT_MAP
from .onvif_events import build_envelope, parse_xml, wsse_header

log = logging.getLogger(__name__)

WS_DISCOVERY_ADDR: tuple[str, int] = ("239.255.255.250", 3702)
DEVICE_NS = "http://www.onvif.org/ver10/device/wsdl"
MEDIA_NS = "http://www.onvif.org/ver10/media/wsdl"
TT_NS = "http://www.onvif.org/ver10/schema"
UNSUPPORTED_VENDOR = "unsupported_probe"
_UNSUPPORTED_MANUFACTURERS = ("hanwha", "wisenet", "samsung techwin")
_MAX_PROFILES_PROBED = 4


@dataclass
class CapabilityReport:
    """Result of probing one device. `recommended_mode` is 'native' or 'local_inference'."""

    device: str
    vendor: str = "unknown"
    model: str | None = None
    firmware: str | None = None
    protocols_ok: dict[str, bool] = field(default_factory=dict)
    native_event_sources: list[str] = field(default_factory=list)
    analytics_events: list[str] = field(default_factory=list)
    io_alarm_inputs: int | None = None
    rtsp_urls: list[str] = field(default_factory=list)
    onvif_profiles: dict[str, bool] = field(
        default_factory=lambda: {"S": False, "T": False, "M": False}
    )
    recommended_mode: str = "local_inference"
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _ProbeError(Exception):
    """A probe got an HTTP error or an unusable response."""


_PROBE_ERRORS = (httpx.HTTPError, ET.ParseError, ValueError, _ProbeError)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _texts(root: ET.Element, local_name: str) -> list[str]:
    return [
        (el.text or "").strip()
        for el in root.iter()
        if _local(el.tag) == local_name and (el.text or "").strip()
    ]


def _first(root: ET.Element, local_name: str) -> str | None:
    values = _texts(root, local_name)
    return values[0] if values else None


def _kv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip()
    return out


def _get_text(client: httpx.Client, url: str, params: dict[str, str] | None = None) -> str:
    resp = client.get(url, params=params)
    if resp.status_code >= 400:
        raise _ProbeError(f"HTTP {resp.status_code}")
    return resp.text


def _get_xml(client: httpx.Client, url: str) -> ET.Element:
    resp = client.get(url)
    if resp.status_code >= 400:
        raise _ProbeError(f"HTTP {resp.status_code}")
    return parse_xml(resp.content)


def _onvif_post(
    client: httpx.Client, url: str, username: str, password: str, body_xml: str
) -> ET.Element:
    envelope = build_envelope(wsse_header(username, password), body_xml)
    resp = client.post(
        url,
        content=envelope.encode("utf-8"),
        headers={"Content-Type": "application/soap+xml; charset=utf-8"},
    )
    if resp.status_code >= 400:
        raise _ProbeError(f"HTTP {resp.status_code}")
    return parse_xml(resp.content)


def _capability_xaddrs(root: ET.Element) -> dict[str, str]:
    """Map service name (Device, Events, Media, Media2, Analytics) to its XAddr."""
    wanted = {"Device", "Events", "Media", "Media2", "Analytics"}
    out: dict[str, str] = {}
    for el in root.iter():
        name = _local(el.tag)
        if name not in wanted or name in out:
            continue
        for child in el:
            if _local(child.tag) == "XAddr" and (child.text or "").strip():
                out[name] = child.text.strip()
                break
    return out


def _probe_isapi(client: httpx.Client, base: str, report: CapabilityReport) -> None:
    try:
        root = _get_xml(client, f"{base}/ISAPI/System/deviceInfo")
    except _PROBE_ERRORS as exc:
        report.protocols_ok["isapi"] = False
        report.reasons.append(f"isapi deviceInfo: {exc}")
        return
    report.protocols_ok["isapi"] = True
    report.vendor = "hikvision"
    report.model = _first(root, "model")
    report.firmware = _first(root, "firmwareVersion")

    try:
        triggers = _get_xml(client, f"{base}/ISAPI/Event/triggers")
        report.analytics_events = sorted(
            {t for t in _texts(triggers, "eventType") if t in ISAPI_EVENT_MAP}
        )
    except _PROBE_ERRORS as exc:
        report.reasons.append(f"isapi event triggers: {exc}")

    try:
        inputs = _get_xml(client, f"{base}/ISAPI/System/IO/inputs")
        report.io_alarm_inputs = sum(1 for el in inputs.iter() if _local(el.tag) == "IOInputPort")
    except _PROBE_ERRORS as exc:
        report.reasons.append(f"isapi IO inputs: {exc}")

    if report.analytics_events:
        report.native_event_sources.append("isapi")


def _probe_dahua(client: httpx.Client, base: str, report: CapabilityReport) -> None:
    cgi = f"{base}/cgi-bin/magicBox.cgi"
    try:
        device_type = _kv(_get_text(client, cgi, {"action": "getDeviceType"})).get("type")
        version = _kv(_get_text(client, cgi, {"action": "getSoftwareVersion"})).get("version")
    except _PROBE_ERRORS as exc:
        report.protocols_ok["dahua_cgi"] = False
        report.reasons.append(f"dahua magicBox: {exc}")
    else:
        report.protocols_ok["dahua_cgi"] = True
        if report.vendor == "unknown":
            report.vendor = "dahua"
        report.model = report.model or device_type
        report.firmware = report.firmware or version

    try:
        with client.stream(
            "GET",
            f"{base}/cgi-bin/eventManager.cgi",
            params={"action": "attach", "codes": "[VideoMotion]"},
        ) as resp:
            if resp.status_code >= 400:
                raise _ProbeError(f"attach HTTP {resp.status_code}")
            ctype = resp.headers.get("content-type", "")
            if "multipart" not in ctype:
                raise _ProbeError(f"attach content-type {ctype!r}")
    except _PROBE_ERRORS as exc:
        report.reasons.append(f"dahua attach: {exc}")
    else:
        report.native_event_sources.append("dahua")


def _probe_onvif(
    client: httpx.Client, base: str, username: str, password: str, report: CapabilityReport
) -> None:
    device_url = f"{base}/onvif/device_service"
    try:
        info = _onvif_post(
            client,
            device_url,
            username,
            password,
            f'<tds:GetDeviceInformation xmlns:tds="{DEVICE_NS}"/>',
        )
    except _PROBE_ERRORS as exc:
        report.protocols_ok["onvif"] = False
        report.reasons.append(f"onvif GetDeviceInformation: {exc}")
        return
    report.protocols_ok["onvif"] = True
    manufacturer = _first(info, "Manufacturer") or ""
    report.model = report.model or _first(info, "Model")
    report.firmware = report.firmware or _first(info, "FirmwareVersion")
    if report.vendor == "unknown" and manufacturer:
        lowered = manufacturer.lower()
        if any(name in lowered for name in _UNSUPPORTED_MANUFACTURERS):
            report.vendor = UNSUPPORTED_VENDOR
            report.reasons.append(
                f"manufacturer {manufacturer!r}: Hanwha SUNAPI probing is not implemented; ONVIF only"
            )
        else:
            report.vendor = lowered

    try:
        caps = _onvif_post(
            client,
            device_url,
            username,
            password,
            f'<tds:GetCapabilities xmlns:tds="{DEVICE_NS}"><tds:Category>All</tds:Category>'
            "</tds:GetCapabilities>",
        )
    except _PROBE_ERRORS as exc:
        report.reasons.append(f"onvif GetCapabilities: {exc}")
        return

    xaddrs = _capability_xaddrs(caps)
    report.onvif_profiles = {
        "S": "Media" in xaddrs,
        "T": "Media2" in xaddrs,
        "M": "Analytics" in xaddrs,
    }
    if "Events" in xaddrs:
        report.native_event_sources.append("onvif")
    if "Media" in xaddrs:
        _probe_onvif_rtsp(client, xaddrs["Media"], username, password, report)


def _probe_onvif_rtsp(
    client: httpx.Client, media_url: str, username: str, password: str, report: CapabilityReport
) -> None:
    try:
        profiles = _onvif_post(
            client,
            media_url,
            username,
            password,
            f'<trt:GetProfiles xmlns:trt="{MEDIA_NS}"/>',
        )
    except _PROBE_ERRORS as exc:
        report.reasons.append(f"onvif GetProfiles: {exc}")
        return
    tokens = [
        el.get("token", "")
        for el in profiles.iter()
        if _local(el.tag) == "Profiles" and el.get("token")
    ][:_MAX_PROFILES_PROBED]
    for token in tokens:
        body = (
            f'<trt:GetStreamUri xmlns:trt="{MEDIA_NS}" xmlns:tt="{TT_NS}">'
            "<trt:StreamSetup><tt:Stream>RTP-Unicast</tt:Stream>"
            "<tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport>"
            "</trt:StreamSetup>"
            f"<trt:ProfileToken>{escape(token)}</trt:ProfileToken>"
            "</trt:GetStreamUri>"
        )
        try:
            root = _onvif_post(client, media_url, username, password, body)
        except _PROBE_ERRORS as exc:
            report.reasons.append(f"onvif GetStreamUri({token}): {exc}")
            continue
        uri = _first(root, "Uri")
        if uri and uri.startswith("rtsp://") and uri not in report.rtsp_urls:
            report.rtsp_urls.append(uri)


def _finalise(report: CapabilityReport) -> None:
    if not any(report.protocols_ok.values()):
        report.reasons.append("no probed protocol answered")
    if report.native_event_sources:
        report.recommended_mode = "native"
    else:
        report.recommended_mode = "local_inference"
        report.reasons.append("no native event source found; local inference required")


def discover(
    host: str,
    username: str,
    password: str,
    *,
    scheme: str = "http",
    port: int = 80,
    base_url: str | None = None,
    timeout: float = 5.0,
    transport: httpx.BaseTransport | None = None,
) -> CapabilityReport:
    """Probe ISAPI, Dahua CGI and ONVIF on one device. Never raises for device errors."""
    base = (base_url or f"{scheme}://{host}:{port}").rstrip("/")
    report = CapabilityReport(device=base)
    digest = httpx.Client(
        auth=httpx.DigestAuth(username, password), timeout=timeout, transport=transport
    )
    plain = httpx.Client(timeout=timeout, transport=transport)
    try:
        _probe_isapi(digest, base, report)
        _probe_dahua(digest, base, report)
        _probe_onvif(plain, base, username, password, report)
    finally:
        digest.close()
        plain.close()
    _finalise(report)
    return report


def _parse_probe_matches(data: bytes) -> list[str]:
    try:
        root = parse_xml(data)
    except _PROBE_ERRORS:
        return []
    out: list[str] = []
    for el in root.iter():
        if _local(el.tag) == "XAddrs" and el.text:
            out.extend(part for part in el.text.split() if part.startswith(("http://", "https://")))
    return out


def ws_discover(
    timeout: float = 3.0,
    *,
    target: tuple[str, int] = WS_DISCOVERY_ADDR,
) -> list[str]:
    """Multicast WS-Discovery Probe for ONVIF NetworkVideoTransmitter devices.

    Returns the deduplicated XAddr URLs of responders within `timeout` seconds.
    Socket errors are logged and the URLs found so far are returned.
    """
    probe = (
        '<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" '
        'xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
        'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
        'xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
        "<e:Header>"
        f"<w:MessageID>uuid:{uuid.uuid4()}</w:MessageID>"
        "<w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>"
        "<w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>"
        "</e:Header><e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types>"
        "</d:Probe></e:Body></e:Envelope>"
    )
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            sock.settimeout(0.25)
            sock.sendto(probe.encode("utf-8"), target)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    data, _addr = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                found.extend(_parse_probe_matches(data))
    except OSError as exc:
        log.warning("ws-discovery failed: %s", exc)
    return list(dict.fromkeys(found))


__all__ = ["CapabilityReport", "UNSUPPORTED_VENDOR", "discover", "ws_discover"]
