"""HTTP fetching, content-hash skipping and payload sniffing.

The single most common real-world failure on Spanish government portals is a
200 OK carrying a cookie wall or error page under a ``text/csv`` content type.
``sniff`` exists to turn that into a loud adapter failure instead of a silently
empty series.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import httpx

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

MAX_BYTES = 120 * 1024 * 1024  # refuse to buffer anything larger


class FetchError(RuntimeError):
    """Network-level or HTTP-level failure."""


class NotFoundError(FetchError):
    """A 4xx that will not resolve itself. Not retried."""


class PayloadError(ValueError):
    """The bytes arrived but are not what the manifest promised."""


@dataclass(frozen=True, slots=True)
class FetchPlan:
    """One concrete thing to download."""

    url: str
    fmt: str  # csv | xls | xlsx | json | html | geojson
    label: str = ""
    encoding: str | None = None
    params: dict[str, Any] | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    optional: bool = False
    """One of many interchangeable fetches. A failure is noted, not fatal.

    Set this when a source is fanned out over many URLs — language editions,
    provinces, months — and the absence of any single one does not invalidate the
    rest. A source with a single authoritative file must leave it False.
    """

    @property
    def cache_key(self) -> str:
        if not self.params:
            return self.url
        query = "&".join(f"{k}={v}" for k, v in sorted(self.params.items()))
        return f"{self.url}?{query}"


@dataclass(slots=True)
class RawPayload:
    plan: FetchPlan
    content: bytes
    sha256: str
    etag: str | None = None
    last_modified: str | None = None
    status: int = 200

    def text(self, encoding: str | None = None) -> str:
        for candidate in (encoding, self.plan.encoding, "utf-8", "latin-1"):
            if candidate is None:
                continue
            try:
                return self.content.decode(candidate)
            except UnicodeDecodeError:
                continue
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.text())


_HTML_MARKERS = (b"<!doctype", b"<html", b"<head", b"<?xml")
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0"
_ZIP_MAGIC = b"PK\x03\x04"


def sniff(payload: RawPayload, *, min_rows: int = 1, expected_columns: Iterable[str] = ()) -> None:
    """Reject payloads that do not structurally match their declared format."""
    body = payload.content
    if not body:
        raise PayloadError(f"empty body from {payload.plan.url}")

    head = body[:512].lstrip().lower()
    fmt = payload.plan.fmt

    if fmt in {"csv", "xls", "xlsx", "json", "geojson"}:
        if any(head.startswith(marker) for marker in _HTML_MARKERS):
            raise PayloadError(
                f"{payload.plan.url} returned an HTML document where {fmt} was expected "
                "(cookie wall or error page)"
            )

    if fmt == "xls" and not body.startswith(_OLE2_MAGIC):
        # Some portals serve .xls URLs that are really xlsx or html tables.
        if body.startswith(_ZIP_MAGIC):
            raise PayloadError(f"{payload.plan.url} declared xls but is a zip/xlsx container")
        raise PayloadError(f"{payload.plan.url} declared xls but lacks OLE2 magic")

    if fmt == "xlsx" and not body.startswith(_ZIP_MAGIC):
        raise PayloadError(f"{payload.plan.url} declared xlsx but lacks zip magic")

    if fmt in {"json", "geojson"}:
        stripped = body.lstrip()[:1]
        if stripped not in (b"{", b"["):
            raise PayloadError(f"{payload.plan.url} declared {fmt} but body starts with {stripped!r}")

    if fmt == "csv":
        text = payload.text()
        lines = [line for line in text.splitlines() if line.strip()]
        if len(lines) < min_rows + 1:
            raise PayloadError(
                f"{payload.plan.url} yielded {len(lines)} non-empty lines, expected at least {min_rows + 1}"
            )
        if expected_columns:
            header = lines[0].lower()
            missing = [c for c in expected_columns if c.lower() not in header]
            if missing:
                raise PayloadError(f"{payload.plan.url} csv header missing columns: {missing}")


class StateStore:
    """Per-URL content hashes and validators, committed so runs are comparable."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, dict[str, Any]] = {}
        if path.exists():
            self._data = json.loads(path.read_text())

    def entry(self, key: str) -> dict[str, Any]:
        return self._data.get(key, {})

    def hash_for(self, key: str) -> str | None:
        return self.entry(key).get("sha256")

    def validators(self, key: str) -> dict[str, str]:
        entry = self.entry(key)
        headers = {}
        if etag := entry.get("etag"):
            headers["If-None-Match"] = etag
        if last_modified := entry.get("last_modified"):
            headers["If-Modified-Since"] = last_modified
        return headers

    def put(self, key: str, payload: RawPayload, *, checked: str) -> None:
        self._data[key] = {
            "sha256": payload.sha256,
            "etag": payload.etag,
            "last_modified": payload.last_modified,
            "bytes": len(payload.content),
            "last_checked": checked,
        }

    def touch(self, key: str, *, checked: str) -> None:
        entry = self._data.setdefault(key, {})
        entry["last_checked"] = checked

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=1, sort_keys=True) + "\n")


class Fetcher:
    """Thin retrying HTTP client. One instance per run, shared by all adapters."""

    def __init__(
        self,
        *,
        timeout: float = 60.0,
        retries: int = 3,
        backoff: float = 2.0,
        offline: bool = False,
    ) -> None:
        self.retries = retries
        self.backoff = backoff
        self.offline = offline
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "es-ES,es;q=0.9,en;q=0.8"},
        )

    def get(self, plan: FetchPlan, *, headers: dict[str, str] | None = None) -> RawPayload | None:
        """Return the payload, or None when the server says 304 Not Modified."""
        # Some sources are files a person committed rather than a URL to fetch —
        # hand-exported search baskets, for instance. They go through the same
        # sniffing, validation and history machinery as anything downloaded.
        if plan.url.startswith("file://"):
            path = Path(plan.url.removeprefix("file://"))
            if not path.exists():
                raise NotFoundError(f"{path} does not exist")
            content = path.read_bytes()
            return RawPayload(
                plan=plan, content=content, sha256=hashlib.sha256(content).hexdigest()
            )

        if self.offline:
            raise FetchError(f"offline mode: refusing to fetch {plan.url}")

        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                response = self._client.get(plan.url, params=plan.params, headers=headers or {})
                if response.status_code == 304:
                    return None
                if response.status_code >= 500 or response.status_code in (408, 429):
                    raise FetchError(f"HTTP {response.status_code} from {plan.url}")
                if 400 <= response.status_code < 500:
                    # A 404 or 403 will still be a 404 or 403 in four seconds.
                    raise NotFoundError(f"HTTP {response.status_code} from {plan.url}")
                response.raise_for_status()
                content = response.content
                if len(content) > MAX_BYTES:
                    raise PayloadError(f"{plan.url} returned {len(content)} bytes, over the size cap")
                return RawPayload(
                    plan=plan,
                    content=content,
                    sha256=hashlib.sha256(content).hexdigest(),
                    etag=response.headers.get("ETag"),
                    last_modified=response.headers.get("Last-Modified"),
                    status=response.status_code,
                )
            except (PayloadError, NotFoundError):
                raise
            except Exception as exc:  # noqa: BLE001 - retried below, re-raised as FetchError
                last_error = exc
                if attempt < self.retries:
                    time.sleep(self.backoff ** attempt)
        raise FetchError(f"failed to fetch {plan.url}: {last_error}")

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
