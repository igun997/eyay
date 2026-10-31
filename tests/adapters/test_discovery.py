from __future__ import annotations

import socket
import threading
from typing import Any

import httpx

from eyeai.adapters.discovery import (
    UNSUPPORTED_VENDOR,
    CapabilityReport,
    _parse_probe_matches,
    discover,
    ws_discover,
)
from tests.adapters.mocks.camera_fixtures import (
    dahua_routes,
    hikvision_routes,
    make_transport,
    onvif_routes,
    static_route,
)

BASE = "http://cam.test"


def _run(routes: dict[str, Any], **kwargs: Any) -> CapabilityReport:
    return discover(
        "cam.test",
        "admin",
        "secret",
        base_url=BASE,
        transport=make_transport(routes),
        **kwargs,
    )


def test_hikvision_device_is_recommended_native() -> None:
    routes = {**hikvision_routes(), **onvif_routes()}

    report = _run(routes)

    assert report.device == BASE
    assert report.vendor == "hikvision"
    assert report.model == "DS-2CD2143G2"
    assert report.firmware == "V5.7.3"
    assert report.protocols_ok["isapi"] is True
    assert report.protocols_ok["onvif"] is True
    assert report.analytics_events == ["VMD", "linedetection"]
    assert report.io_alarm_inputs == 2
    assert "isapi" in report.native_event_sources
    assert "onvif" in report.native_event_sources
    assert report.recommended_mode == "native"


def test_isapi_triggers_are_filtered_to_known_event_types() -> None:
    routes = hikvision_routes(analytics=("linedetection", "VMD", "notARealTrigger"))

    report = _run(routes)

    assert report.analytics_events == ["VMD", "linedetection"]
    assert "notARealTrigger" not in report.analytics_events


def test_isapi_without_known_triggers_is_not_a_native_source() -> None:
    routes = hikvision_routes(analytics=("notARealTrigger",))

    report = _run(routes)

    assert report.protocols_ok["isapi"] is True
    assert report.analytics_events == []
    assert "isapi" not in report.native_event_sources
    assert report.recommended_mode == "local_inference"


def test_isapi_io_input_count_follows_device_response() -> None:
    report = _run(hikvision_routes(io_inputs=0))

    assert report.io_alarm_inputs == 0


def test_isapi_missing_device_info_records_failure_and_continues() -> None:
    report = _run({})

    assert report.protocols_ok["isapi"] is False
    assert any(reason.startswith("isapi deviceInfo: HTTP 404") for reason in report.reasons)
    assert report.vendor == "unknown"
    assert report.recommended_mode == "local_inference"


def test_dahua_device_with_attach_is_native() -> None:
    report = _run(dahua_routes(has_attach=True))

    assert report.protocols_ok["dahua_cgi"] is True
    assert report.vendor == "dahua"
    assert report.model == "IPC-HFW2431"
    assert report.firmware == "2.800.0000000.20"
    assert report.native_event_sources == ["dahua"]
    assert report.recommended_mode == "native"


def test_dahua_without_multipart_attach_is_not_native() -> None:
    report = _run(dahua_routes(has_attach=False))

    assert report.protocols_ok["dahua_cgi"] is True
    assert report.vendor == "dahua"
    assert "dahua" not in report.native_event_sources
    assert any(reason.startswith("dahua attach:") for reason in report.reasons)
    assert report.recommended_mode == "local_inference"


def test_dahua_attach_with_non_multipart_content_type_is_rejected() -> None:
    routes = dahua_routes(has_attach=False)
    routes["/cgi-bin/eventManager.cgi"] = static_route(200, b"ok", content_type="text/plain")

    report = _run(routes)

    assert "dahua" not in report.native_event_sources
    assert any("attach content-type 'text/plain'" in reason for reason in report.reasons)


def test_no_protocol_answering_is_reported_as_local_inference() -> None:
    report = _run({})

    assert not any(report.protocols_ok.values())
    assert "no probed protocol answered" in report.reasons
    assert report.native_event_sources == []
    assert report.recommended_mode == "local_inference"
    assert "no native event source found; local inference required" in report.reasons


def test_onvif_only_device_with_events_service_is_native() -> None:
    report = _run(onvif_routes(services=("Device", "Events", "Media")))

    assert report.protocols_ok["onvif"] is True
    assert report.vendor == "hikvision"
    assert report.native_event_sources == ["onvif"]
    assert report.recommended_mode == "native"


def test_onvif_without_events_service_is_local_inference() -> None:
    report = _run(onvif_routes(services=("Device", "Media")))

    assert report.protocols_ok["onvif"] is True
    assert report.native_event_sources == []
    assert report.recommended_mode == "local_inference"


def test_onvif_profile_flags_reflect_advertised_services() -> None:
    report = _run(onvif_routes(services=("Device", "Media", "Analytics")))

    assert report.onvif_profiles == {"S": True, "T": False, "M": True}


def test_onvif_rtsp_stream_uri_is_collected_once() -> None:
    uri = "rtsp://user:pass@cam.test:554/Streaming/Channels/101"

    report = _run(onvif_routes(tokens=("P1", "P2", "P3"), rtsp_uri=uri))

    assert report.rtsp_urls == [uri]


def test_onvif_stream_uri_probing_is_capped_at_four_profiles() -> None:
    routes = onvif_routes(tokens=("P1", "P2", "P3", "P4", "P5", "P6"))
    media = routes["/onvif/media"]
    stream_calls: list[bytes] = []

    def counting(request: httpx.Request) -> httpx.Response:
        if b"GetStreamUri" in request.content:
            stream_calls.append(request.content)
        return media(request)

    routes["/onvif/media"] = counting

    _run(routes)

    assert len(stream_calls) == 4


def test_hanwha_manufacturer_is_marked_unsupported_probe() -> None:
    routes = onvif_routes(manufacturer="Hanwha Techwin", services=("Device", "Media"))

    report = _run(routes)

    assert report.vendor == UNSUPPORTED_VENDOR
    assert any("Hanwha SUNAPI probing is not implemented" in r for r in report.reasons)


def test_report_to_dict_exposes_every_field_as_plain_data() -> None:
    report = _run({**hikvision_routes(), **onvif_routes()})

    data = report.to_dict()

    assert data["device"] == BASE
    assert data["vendor"] == "hikvision"
    assert data["recommended_mode"] == "native"
    assert data["native_event_sources"] == report.native_event_sources
    assert data["onvif_profiles"] == report.onvif_profiles
    assert set(data) == {
        "device",
        "vendor",
        "model",
        "firmware",
        "protocols_ok",
        "native_event_sources",
        "analytics_events",
        "io_alarm_inputs",
        "rtsp_urls",
        "onvif_profiles",
        "recommended_mode",
        "reasons",
    }


def test_base_url_is_built_from_scheme_host_and_port() -> None:
    seen: list[str] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(404, content=b"no")

    report = discover(
        "cam.test",
        "admin",
        "secret",
        scheme="https",
        port=8443,
        transport=httpx.MockTransport(record),
    )

    assert report.device == "https://cam.test:8443"
    assert seen and all(url.startswith("https://cam.test:8443/") for url in seen)


def test_trailing_slash_in_base_url_is_stripped() -> None:
    report = discover(
        "cam.test",
        "admin",
        "secret",
        base_url="http://cam.test/",
        transport=make_transport({}),
    )

    assert report.device == "http://cam.test"


def test_parse_probe_matches_keeps_only_http_xaddrs() -> None:
    xml = (
        b'<?xml version="1.0"?>'
        b'<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" '
        b'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">'
        b"<e:Body><d:ProbeMatches><d:ProbeMatch><d:XAddrs>"
        b"http://10.0.0.5/onvif/device_service rtsp://10.0.0.9/x https://10.0.0.6/onvif"
        b"</d:XAddrs></d:ProbeMatch></d:ProbeMatches></e:Body></e:Envelope>"
    )

    assert _parse_probe_matches(xml) == [
        "http://10.0.0.5/onvif/device_service",
        "https://10.0.0.6/onvif",
    ]


def test_parse_probe_matches_returns_empty_for_garbage() -> None:
    assert _parse_probe_matches(b"not xml at all <<<") == []


def test_ws_discover_collects_and_deduplicates_responder_xaddrs() -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("127.0.0.1", 0))
    server.settimeout(2.0)
    target = server.getsockname()
    probes: list[bytes] = []

    def reply_once() -> None:
        data, addr = server.recvfrom(65535)
        probes.append(data)
        first = (
            b'<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" '
            b'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">'
            b"<e:Body><d:ProbeMatches><d:ProbeMatch><d:XAddrs>"
            b"http://10.0.0.5/onvif/device_service http://10.0.0.6/onvif/device_service"
            b"</d:XAddrs></d:ProbeMatch></d:ProbeMatches></e:Body></e:Envelope>"
        )
        duplicate = first.replace(
            b"http://10.0.0.6/onvif/device_service", b"http://10.0.0.5/onvif/device_service"
        )
        server.sendto(first, addr)
        server.sendto(duplicate, addr)

    worker = threading.Thread(target=reply_once)
    worker.start()
    try:
        found = ws_discover(timeout=0.6, target=target)
    finally:
        worker.join(timeout=5.0)
        server.close()

    assert probes and b"NetworkVideoTransmitter" in probes[0]
    assert found == [
        "http://10.0.0.5/onvif/device_service",
        "http://10.0.0.6/onvif/device_service",
    ]


def test_ws_discover_returns_empty_when_nobody_answers() -> None:
    silent = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    silent.bind(("127.0.0.1", 0))
    try:
        found = ws_discover(timeout=0.3, target=silent.getsockname())
    finally:
        silent.close()

    assert found == []
