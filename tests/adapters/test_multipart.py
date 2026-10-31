from __future__ import annotations

import pytest

from eyeai.adapters._multipart import MultipartSplitter, boundary_from_content_type


def test_boundary_extracted_from_content_type() -> None:
    assert boundary_from_content_type("multipart/mixed; boundary=abc") == b"abc"


def test_boundary_quoted_is_unquoted() -> None:
    assert boundary_from_content_type('multipart/mixed; boundary="xyz"') == b"xyz"


def test_boundary_missing_returns_none() -> None:
    assert boundary_from_content_type("application/xml") is None


def test_empty_boundary_rejected() -> None:
    with pytest.raises(ValueError, match="boundary is required"):
        MultipartSplitter(b"")


def test_splits_two_parts_in_one_feed() -> None:
    splitter = MultipartSplitter(b"boundary")
    data = (
        b"--boundary\r\nContent-Type: application/xml\r\n\r\n<a/>\r\n"
        b"--boundary\r\nContent-Type: application/xml\r\n\r\n<b/>\r\n"
        b"--boundary--\r\n"
    )
    parts = splitter.feed(data)
    assert [p.body for p in parts] == [b"<a/>", b"<b/>"]
    assert parts[0].headers.get("content-type") == "application/xml"


def test_part_split_across_feeds_is_reassembled() -> None:
    splitter = MultipartSplitter(b"boundary")
    raw = b"--boundary\r\nContent-Type: text/plain\r\n\r\nhello world\r\n--boundary--\r\n"
    collected = []
    for i in range(0, len(raw), 7):
        collected.extend(splitter.feed(raw[i : i + 7]))
    assert [p.body for p in collected] == [b"hello world"]
    assert splitter.closed
