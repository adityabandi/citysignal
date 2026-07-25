"""Payload sniffing.

The single most common real failure on Spanish government portals is HTTP 200
carrying a cookie wall or error page under a `text/csv` content type. If that
parses to an empty frame the series silently flatlines, which is worse than an
outage because nobody notices. These tests pin the loud failure.
"""

from __future__ import annotations

import pytest

from citysignal.framework.fetch import FetchPlan, PayloadError, RawPayload, sniff


def payload(content: bytes, fmt: str = "csv") -> RawPayload:
    return RawPayload(
        plan=FetchPlan(url="https://example.test/data", fmt=fmt),
        content=content,
        sha256="x",
    )


def test_html_masquerading_as_csv_is_rejected():
    body = b"<!DOCTYPE html>\n<html><head><title>Cookies</title></head><body>Accept</body></html>"
    with pytest.raises(PayloadError, match="HTML document"):
        sniff(payload(body))


def test_html_masquerading_as_json_is_rejected():
    with pytest.raises(PayloadError):
        sniff(payload(b"<html><body>503</body></html>", fmt="json"))


def test_xls_without_ole2_magic_is_rejected():
    with pytest.raises(PayloadError, match="OLE2"):
        sniff(payload(b"not a spreadsheet at all", fmt="xls"))


def test_xlsx_declared_but_served_as_xls_is_caught():
    with pytest.raises(PayloadError, match="zip magic"):
        sniff(payload(b"\xd0\xcf\x11\xe0rest", fmt="xlsx"))


def test_xls_that_is_really_a_zip_is_caught():
    with pytest.raises(PayloadError, match="zip/xlsx"):
        sniff(payload(b"PK\x03\x04rest", fmt="xls"))


def test_empty_body_is_rejected():
    with pytest.raises(PayloadError, match="empty"):
        sniff(payload(b""))


def test_csv_below_the_minimum_row_count_is_rejected():
    with pytest.raises(PayloadError, match="non-empty lines"):
        sniff(payload(b"header_only\n"), min_rows=1)


def test_csv_missing_a_required_column_is_rejected():
    body = b"municipio;valor\n28079;12\n"
    with pytest.raises(PayloadError, match="missing columns"):
        sniff(payload(body), expected_columns=("periodo",))


def test_a_genuine_csv_passes():
    body = b"municipio;periodo;valor\n28079;2026-01;12\n08019;2026-01;9\n"
    sniff(payload(body), min_rows=2, expected_columns=("municipio", "periodo"))


def test_a_genuine_json_passes():
    sniff(payload(b'{"items": []}', fmt="json"))
    sniff(payload(b"[1, 2, 3]", fmt="json"))
