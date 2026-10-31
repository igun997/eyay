"""Incremental multipart/mixed splitter used by camera event streams.

Handles both `Content-Length`-framed parts (Hikvision ISAPI alertStream) and
parts delimited only by the next boundary (Dahua eventManager fallback).
"""
from __future__ import annotations

from dataclasses import dataclass, field


def boundary_from_content_type(content_type: str) -> bytes | None:
    """Extract the boundary token from a Content-Type header value."""
    for piece in content_type.split(";"):
        piece = piece.strip()
        if piece.lower().startswith("boundary="):
            token = piece.split("=", 1)[1].strip().strip('"')
            return token.encode("latin-1") if token else None
    return None


@dataclass
class Part:
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


class MultipartSplitter:
    """Feed raw bytes, receive complete parts. Keeps partial data buffered."""

    def __init__(self, boundary: bytes) -> None:
        if not boundary:
            raise ValueError("multipart boundary is required")
        self._delim = b"--" + boundary
        self._buf = bytearray()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def feed(self, data: bytes) -> list[Part]:
        if self._closed:
            return []
        self._buf += data
        parts: list[Part] = []
        while True:
            start = self._buf.find(self._delim)
            if start < 0:
                keep = len(self._delim) + 3
                if len(self._buf) > keep:
                    del self._buf[:-keep]
                return parts
            if start > 0:
                del self._buf[:start]
            after = len(self._delim)
            if len(self._buf) < after + 2:
                return parts
            if self._buf[after:after + 2] == b"--":
                self._closed = True
                self._buf.clear()
                return parts
            hdr_end = self._buf.find(b"\r\n\r\n", after)
            if hdr_end < 0:
                return parts
            headers = self._parse_headers(bytes(self._buf[after:hdr_end]))
            body_start = hdr_end + 4
            length = headers.get("content-length", "")
            if length.isdigit():
                n = int(length)
                if len(self._buf) < body_start + n:
                    return parts
                body = bytes(self._buf[body_start:body_start + n])
                del self._buf[:body_start + n]
            else:
                nxt = self._buf.find(self._delim, body_start)
                if nxt < 0:
                    return parts
                body = bytes(self._buf[body_start:nxt]).rstrip(b"\r\n")
                del self._buf[:nxt]
            parts.append(Part(headers=headers, body=body))

    @staticmethod
    def _parse_headers(block: bytes) -> dict[str, str]:
        headers: dict[str, str] = {}
        for line in block.decode("latin-1").split("\r\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip().lower()] = value.strip()
        return headers
