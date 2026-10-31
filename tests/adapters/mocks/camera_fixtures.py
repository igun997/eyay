"""Wire-format builders for camera protocols used by adapter tests.

Everything here is hand-written from the vendor documentation shape (ISAPI alertStream
multipart XML, Dahua eventManager multipart text, ONVIF SOAP). None of it was captured
from real hardware.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any

import httpx


Route = Callable[[httpx.Request], httpx.Response]

ISAPI_NS = "http://www.isapi.org/ver20/XMLSchema"
BOUNDARY = "boundary"


def multipart_content_type(boundary: str = BOUNDARY) -> str:
    return f"multipart/mixed; boundary={boundary}"


# --- ISAPI alertStream -------------------------------------------------------


def isapi_event_xml(
    event_type: str,
    state: str = "active",
    date_time: str | None = "2026-01-15T10:00:00+07:00",
    channel: int | str | None = 1,
    extra: dict[str, str] | None = None,
) -> str:
    parts = [f'<EventNotificationAlert version="2.0" xmlns="{ISAPI_NS}">']
    parts.append(f"<eventType>{event_type}</eventType>")
    parts.append(f"<eventState>{state}</eventState>")
    if date_time is not None:
        parts.append(f"<dateTime>{date_time}</dateTime>")
    if channel is not None:
        parts.append(f"<channelID>{channel}</channelID>")
    for key, value in (extra or {}).items():
        parts.append(f"<{key}>{value}</{key}>")
    parts.append("</EventNotificationAlert>")
    return "".join(parts)


def isapi_part(xml: str, boundary: str = BOUNDARY) -> bytes:
    body = xml.encode("utf-8")
    head = (
        f"--{boundary}\r\n"
        'Content-Type: application/xml; charset="UTF-8"\r\n'
        f"Content-Length: {len(body)}\r\n"
        "\r\n"
    ).encode("utf-8")
    return head + body + b"\r\n"


def isapi_stream(*xmls: str, boundary: str = BOUNDARY) -> bytes:
    out = b"".join(isapi_part(x, boundary) for x in xmls)
    return out + f"--{boundary}--\r\n".encode("utf-8")


# --- Dahua eventManager --------------------------------------------------------


def dahua_part(
    code: str,
    action: str,
    index: int | None,
    data: dict[str, Any] | None,
    boundary: str = BOUNDARY,
) -> bytes:
    head_line = f"Code={code};action={action}"
    if index is not None:
        head_line += f";index={index}"
    payload = "" if data is None else "data=" + json.dumps(data)
    body = (head_line + "\r\n" + payload).encode("utf-8")
    header = (
        f"--{boundary}\r\n"
        "Content-Type: text/plain\r\n"
        f"Content-Length: {len(body)}\r\n"
        "\r\n"
    ).encode("utf-8")
    return header + body + b"\r\n"


def dahua_stream(*parts: bytes, boundary: str = BOUNDARY) -> bytes:
    return b"".join(parts) + f"--{boundary}--\r\n".encode("utf-8")


# --- ONVIF SOAP ------------------------------------------------------------------


def soap_envelope(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
        f"<s:Body>{body}</s:Body></s:Envelope>"
    )


def onvif_device_information(
    manufacturer: str = "Hikvision",
    model: str = "DS-2CD2143G2",
    firmware: str = "V5.7.3",
) -> str:
    return soap_envelope(
        '<tds:GetDeviceInformationResponse xmlns:tds="http://www.onvif.org/ver10/device/wsdl">'
        f"<tds:Manufacturer>{manufacturer}</tds:Manufacturer>"
        f"<tds:Model>{model}</tds:Model>"
        f"<tds:FirmwareVersion>{firmware}</tds:FirmwareVersion>"
        "<tds:SerialNumber>SN0001</tds:SerialNumber>"
        "</tds:GetDeviceInformationResponse>"
    )


def onvif_capabilities(
    base: str = "http://cam.test",
    services: Iterable[str] = ("Device", "Events", "Media", "Analytics"),
) -> str:
    entries = "".join(f"<tt:{name}><tt:XAddr>{base}/onvif/{name.lower()}</tt:XAddr></tt:{name}>" for name in services)
    return soap_envelope(
        '<tds:GetCapabilitiesResponse xmlns:tds="http://www.onvif.org/ver10/device/wsdl" '
        'xmlns:tt="http://www.onvif.org/ver10/schema">'
        f"<tds:Capabilities>{entries}</tds:Capabilities>"
        "</tds:GetCapabilitiesResponse>"
    )


def onvif_profiles(tokens: Iterable[str] = ("P1", "P2")) -> str:
    items = "".join(f'<trt:Profiles token="{t}" fixed="true"/>' for t in tokens)
    return soap_envelope(
        '<trt:GetProfilesResponse xmlns:trt="http://www.onvif.org/ver10/media/wsdl">'
        f"{items}</trt:GetProfilesResponse>"
    )


def onvif_stream_uri(uri: str) -> str:
    return soap_envelope(
        '<trt:GetStreamUriResponse xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
        'xmlns:tt="http://www.onvif.org/ver10/schema">'
        f"<trt:MediaUri><tt:Uri>{uri}</tt:Uri></trt:MediaUri></trt:GetStreamUriResponse>"
    )


def onvif_pull_response(messages: Iterable[dict[str, Any]]) -> str:
    """Build a PullMessagesResponse.

    Each message dict: ``topic``, ``utc`` (str or None), ``operation`` (str or None),
    ``source`` (dict name->value), ``data`` (dict name->value).
    """
    rendered = []
    for msg in messages:
        attrs = ""
        if msg.get("utc") is not None:
            attrs += f' UtcTime="{msg["utc"]}"'
        if msg.get("operation") is not None:
            attrs += f' PropertyOperation="{msg["operation"]}"'
        source = "".join(
            f'<tt:SimpleItem Name="{k}" Value="{v}"/>' for k, v in (msg.get("source") or {}).items()
        )
        data = "".join(
            f'<tt:SimpleItem Name="{k}" Value="{v}"/>' for k, v in (msg.get("data") or {}).items()
        )
        rendered.append(
            "<wsnt:NotificationMessage>"
            f"<wsnt:Topic>{msg['topic']}</wsnt:Topic>"
            f"<wsnt:Message><tt:Message{attrs}>"
            f"<tt:Source>{source}</tt:Source>"
            f"<tt:Data>{data}</tt:Data>"
            "</tt:Message></wsnt:Message>"
            "</wsnt:NotificationMessage>"
        )
    return soap_envelope(
        '<tev:PullMessagesResponse xmlns:tev="http://www.onvif.org/ver10/events/wsdl" '
        'xmlns:wsnt="http://docs.oasis-open.org/wsn/b-2" '
        'xmlns:tt="http://www.onvif.org/ver10/schema">'
        + "".join(rendered)
        + "</tev:PullMessagesResponse>"
    )


# --- Routing -------------------------------------------------------------------


def static_route(status: int, body: bytes | str, content_type: str = "application/xml") -> Route:
    payload = body.encode("utf-8") if isinstance(body, str) else body

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers={"content-type": content_type}, content=payload)

    return handler


def soap_route(responses: dict[str, str]) -> Route:
    """Pick the response whose operation name appears in the SOAP request body."""

    def handler(request: httpx.Request) -> httpx.Response:
        text = request.content.decode("utf-8", errors="replace")
        for operation, body in responses.items():
            if f"<{operation}" in text or f":{operation}" in text:
                return httpx.Response(200, headers={"content-type": "application/soap+xml"}, content=body.encode())
        return httpx.Response(500, content=b"no matching operation")

    return handler


def make_transport(routes: dict[str, Route]) -> httpx.MockTransport:
    """Route by URL path. Unknown paths answer 404 so probes record a failure."""

    def handler(request: httpx.Request) -> httpx.Response:
        route = routes.get(request.url.path)
        if route is None:
            return httpx.Response(404, content=b"not found")
        return route(request)

    return httpx.MockTransport(handler)


def hikvision_routes(
    *,
    analytics: tuple[str, ...] = ("linedetection", "VMD"),
    io_inputs: int = 2,
    device_model: str = "DS-2CD2143G2",
) -> dict[str, Route]:
    """Routes for a Hikvision-like ISAPI device that answers every probe."""
    triggers = "".join(
        f"<EventTrigger><eventType>{t}</eventType></EventTrigger>" for t in analytics
    )
    inputs = "".join(f'<IOInputPort id="{i + 1}"/>' for i in range(io_inputs))
    return {
        "/ISAPI/System/deviceInfo": static_route(
            200,
            f'<DeviceInfo xmlns="{ISAPI_NS}"><model>{device_model}</model>'
            "<firmwareVersion>V5.7.3</firmwareVersion></DeviceInfo>",
        ),
        "/ISAPI/Event/triggers": static_route(
            200, f'<EventTriggerList xmlns="{ISAPI_NS}">{triggers}</EventTriggerList>'
        ),
        "/ISAPI/System/IO/inputs": static_route(
            200, f'<IOPortList xmlns="{ISAPI_NS}">{inputs}</IOPortList>'
        ),
    }


def dahua_routes(*, has_attach: bool = True) -> dict[str, Route]:
    routes: dict[str, Route] = {
        "/cgi-bin/magicBox.cgi": lambda req: httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=(
                b"type=IPC-HFW2431\r\nversion=2.800.0000000.20\r\n"
                if req.url.params.get("action") == "getDeviceType"
                else b"version=2.800.0000000.20\r\n"
            ),
        ),
    }
    if has_attach:
        routes["/cgi-bin/eventManager.cgi"] = static_route(
            200, b"--boundary\r\n", content_type=multipart_content_type()
        )
    return routes


def onvif_routes(
    *,
    manufacturer: str = "Hikvision",
    services: tuple[str, ...] = ("Device", "Events", "Media", "Analytics"),
    tokens: tuple[str, ...] = ("P1", "P2"),
    rtsp_uri: str = "rtsp://user:pass@cam.test:554/Streaming/Channels/101",
    base: str = "http://cam.test",
) -> dict[str, Route]:
    device = soap_route(
        {
            "GetDeviceInformation": onvif_device_information(manufacturer=manufacturer),
            "GetCapabilities": onvif_capabilities(base=base, services=services),
        }
    )
    media = soap_route(
        {
            "GetProfiles": onvif_profiles(tokens),
            "GetStreamUri": onvif_stream_uri(rtsp_uri),
        }
    )
    return {"/onvif/device_service": device, "/onvif/media": media}
