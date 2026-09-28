"""Shared test helpers: a recording ``httpx.MockTransport`` and envelope builders (no network)."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from ninjavault_cdn import AsyncNinjaVaultCdnClient, NinjaVaultCdnClient

BASE_URL = "https://cdn.test"
API_KEY = "cdn_xxxxx"

Handler = Callable[[httpx.Request], httpx.Response]


def ok(data: Any, status_code: int = 200, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        status_code, json={"success": True, "data": data, "error": None}, headers=headers
    )


def fail(
    status_code: int,
    error_code: int,
    message: str,
    *,
    description: str | None = None,
    trace_id: str | None = "trace-123",
    details: dict[str, list[str]] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return httpx.Response(
        status_code,
        json={
            "success": False,
            "data": None,
            "error": {
                "errorCode": error_code,
                "message": message,
                "description": description,
                "traceId": trace_id,
                "details": details,
            },
        },
        headers=headers,
    )


@dataclass
class Recorder:
    """Records every request and answers with a queue of responses (or one fixed response)."""

    responses: list[httpx.Response | Handler] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        if not self.responses:
            raise AssertionError(f"Unexpected request {request.method} {request.url}")
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return item(request) if callable(item) else item

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]

    def json_body(self) -> Any:
        return json.loads(self.last.content)


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def client(recorder: Recorder) -> Iterator[NinjaVaultCdnClient]:
    http = httpx.Client(transport=httpx.MockTransport(recorder))
    with NinjaVaultCdnClient(base_url=BASE_URL, api_key=API_KEY, http_client=http) as cdn:
        yield cdn
    http.close()


@pytest.fixture
async def aclient(recorder: Recorder) -> Any:
    http = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    async with AsyncNinjaVaultCdnClient(
        base_url=BASE_URL, api_key=API_KEY, http_client=http
    ) as cdn:
        yield cdn
    await http.aclose()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "NINJAVAULT_CDN_BASE_URL",
        "NINJAVAULT_CDN_API_KEY",
        "NINJAVAULT_CDN_PUBLIC_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def parse_multipart(request: httpx.Request) -> dict[str, tuple[dict[str, str], bytes]]:
    """Split a multipart/form-data body into ``{field name: (part headers, part body)}``."""
    content_type = request.headers["Content-Type"]
    assert content_type.startswith("multipart/form-data")
    boundary = content_type.split("boundary=", 1)[1].encode()
    parts: dict[str, tuple[dict[str, str], bytes]] = {}
    for chunk in request.content.split(b"--" + boundary):
        raw = chunk.strip(b"\r\n")
        if not raw or raw == b"--":
            continue
        head, _, body = raw.partition(b"\r\n\r\n")
        headers: dict[str, str] = {}
        for line in head.decode().split("\r\n"):
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        disposition = headers["content-disposition"]
        field_name = disposition.split('name="', 1)[1].split('"', 1)[0]
        parts[field_name] = (headers, body)
    return parts


FILE_OBJECT: dict[str, Any] = {
    "id": "0f8fad5b-d9cb-469f-a165-70867728950e",
    "tenantId": "42",
    "ownerId": 1001,
    "bucket": "documents",
    "objectKey": "t/42/o/abc/reports/2026/invoice.pdf",
    "originalFileName": "invoice.pdf",
    "contentType": "application/pdf",
    "sizeBytes": 2048,
    "checksum": "ab12",
    "visibility": "Private",
    "extension": "pdf",
    "category": "Document",
    "thumbnailStatus": "NotApplicable",
    "thumbnailUrl": None,
    "createdAtUTC": "2026-09-28T10:11:12.1234567Z",
    "url": "https://cdn.test/api/v1/files/documents/t/42/o/abc/reports/2026/invoice.pdf",
    "viewUrl": None,
}
