"""Async parity tests for AsyncNinjaVaultCdnClient."""

from __future__ import annotations

import io
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from ninjavault_cdn import (
    AsyncNinjaVaultCdnClient,
    BucketVisibility,
    CdnApiError,
    CdnConnectionError,
    CdnTimeoutError,
    ErrorCode,
    FileCategory,
    FileListQuery,
    PresignFailureReason,
    __version__,
)

from .conftest import API_KEY, BASE_URL, FILE_OBJECT, Recorder, fail, ok, parse_multipart


def _file_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        content=b"abc" * 1000,
        headers={
            "Content-Type": "image/png",
            "Content-Disposition": "attachment; filename=logo.png",
        },
    )


async def test_access_context_and_headers(
    aclient: AsyncNinjaVaultCdnClient, recorder: Recorder
) -> None:
    recorder.responses.append(
        ok(
            {
                "name": "svc",
                "isUnrestricted": True,
                "isAdmin": False,
                "allowedBuckets": [],
                "allowedTenantIds": [],
                "buckets": [],
            }
        )
    )

    access = await aclient.get_access_context()

    assert access.is_unrestricted is True
    assert str(recorder.last.url) == f"{BASE_URL}/api/v1/me"
    assert recorder.last.headers["X-Api-Key"] == API_KEY
    assert recorder.last.headers["User-Agent"] == f"ninjavault-cdn-python/{__version__}"


async def test_list_buckets_and_summary(
    aclient: AsyncNinjaVaultCdnClient, recorder: Recorder
) -> None:
    recorder.responses.extend(
        [
            ok([{"name": "assets", "visibility": "Public", "fileCount": 1, "totalSizeBytes": 2}]),
            ok({"totalFileCount": 1, "totalSizeBytes": 2, "categories": []}),
        ]
    )

    buckets = await aclient.list_buckets()
    summary = await aclient.get_summary("assets")

    assert buckets[0].visibility is BucketVisibility.PUBLIC
    assert summary.total_size_bytes == 2
    assert str(recorder.last.url) == f"{BASE_URL}/api/v1/files/summary?bucket=assets"


async def test_upload_multipart(
    aclient: AsyncNinjaVaultCdnClient, recorder: Recorder, tmp_path: Path
) -> None:
    recorder.responses.append(
        ok(
            {
                "id": "1",
                "bucket": "documents",
                "objectKey": "k",
                "visibility": "Public",
                "viewUrl": "v",
            }
        )
    )
    source = tmp_path / "doc.pdf"
    source.write_bytes(b"%PDF")

    result = await aclient.upload("documents", 7, source, owner_id=5, folder_path="a/b")

    parts = parse_multipart(recorder.last)
    assert list(parts) == ["Bucket", "TenantId", "OwnerId", "FolderPath", "File"]
    assert parts["TenantId"][1] == b"7"
    assert 'filename="doc.pdf"' in parts["File"][0]["content-disposition"]
    assert parts["File"][0]["content-type"] == "application/pdf"
    assert parts["File"][1] == b"%PDF"
    assert result.view_url == "v"
    source.unlink()


async def test_list_files_metadata_and_encoding(
    aclient: AsyncNinjaVaultCdnClient, recorder: Recorder
) -> None:
    recorder.responses.extend(
        [
            ok({"items": [FILE_OBJECT], "totalCount": 1, "page": 1, "pageSize": 50}),
            ok(FILE_OBJECT),
        ]
    )

    page = await aclient.list_files(FileListQuery(bucket="documents"), category=FileCategory.IMAGE)
    assert str(recorder.last.url) == f"{BASE_URL}/api/v1/files?bucket=documents&category=Image"
    assert page.items[0].category is FileCategory.DOCUMENT

    await aclient.get_metadata("my docs", "a/ü b")
    assert recorder.last.url.raw_path == b"/api/v1/file-metadata/my%20docs/a/%C3%BC%20b"


async def test_presign_single_and_batch(
    aclient: AsyncNinjaVaultCdnClient, recorder: Recorder
) -> None:
    recorder.responses.extend(
        [
            ok(
                {
                    "url": "https://cdn.test/files/presigned/d/k?sig=1",
                    "expiresAtUtc": "2026-09-28T10:00:00Z",
                }
            ),
            ok(
                {
                    "succeeded": [],
                    "failed": [{"bucket": "d", "objectKey": "k", "reason": "Forbidden"}],
                }
            ),
        ]
    )

    link = await aclient.create_presigned_url("d", "k", 60)
    assert recorder.json_body() == {"bucket": "d", "objectKey": "k", "expirySeconds": 60}
    assert link.url.endswith("sig=1")

    batch = await aclient.create_presigned_urls([("d", "k")])
    assert recorder.json_body() == {"targets": [{"bucket": "d", "objectKey": "k"}]}
    assert batch.failed[0].reason is PresignFailureReason.FORBIDDEN


async def test_delete_variants(aclient: AsyncNinjaVaultCdnClient, recorder: Recorder) -> None:
    recorder.responses.extend(
        [
            httpx.Response(204),
            httpx.Response(
                200, json={"success": False, "error": {"errorCode": 40401, "message": "Gone"}}
            ),
        ]
    )

    await aclient.delete("d", "k")
    assert recorder.last.method == "DELETE"

    with pytest.raises(CdnApiError) as caught:
        await aclient.delete("d", "k")
    assert caught.value.error_code == ErrorCode.NOT_FOUND


async def test_errors_and_retry_after(
    aclient: AsyncNinjaVaultCdnClient, recorder: Recorder
) -> None:
    recorder.responses.append(
        fail(429, 42901, "Slow down", trace_id="t-2", headers={"Retry-After": "3"})
    )

    with pytest.raises(CdnApiError) as caught:
        await aclient.list_buckets()

    assert caught.value.status_code == 429
    assert caught.value.correlation_id == "t-2"
    assert caught.value.retry_after == 3.0


async def test_transport_errors_are_wrapped(
    aclient: AsyncNinjaVaultCdnClient, recorder: Recorder
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    recorder.responses.extend([refuse, slow])
    with pytest.raises(CdnConnectionError):
        await aclient.list_buckets()
    with pytest.raises(CdnTimeoutError):
        await aclient.list_buckets()


async def test_download_async_with(aclient: AsyncNinjaVaultCdnClient, recorder: Recorder) -> None:
    recorder.responses.append(_file_response)

    async with aclient.download("assets", "logos/logo.png") as download:
        assert download.content_type == "image/png"
        assert download.file_name == "logo.png"
        assert download.size_bytes == 3000
        chunks = [chunk async for chunk in download.aiter_bytes(1000)]

    assert b"".join(chunks) == b"abc" * 1000
    assert recorder.last.url.raw_path == b"/api/v1/files/assets/logos/logo.png"


async def test_download_awaited_and_read(
    aclient: AsyncNinjaVaultCdnClient, recorder: Recorder
) -> None:
    recorder.responses.append(_file_response)

    download = await aclient.download("assets", "logo.png")
    try:
        assert await download.read() == b"abc" * 1000
    finally:
        await download.aclose()

    async with await aclient.download("assets", "logo.png") as again:
        assert await again.read() == b"abc" * 1000


async def test_download_error(aclient: AsyncNinjaVaultCdnClient, recorder: Recorder) -> None:
    recorder.responses.append(fail(403, 40301, "Forbidden"))
    with pytest.raises(CdnApiError) as caught:
        async with aclient.download("private", "k"):
            pass  # pragma: no cover
    assert caught.value.error_code == ErrorCode.FORBIDDEN


def test_download_validates_arguments_eagerly(aclient: Any) -> None:
    with pytest.raises(ValueError, match="bucket"):
        aclient.download("", "k")


async def test_download_to(
    aclient: AsyncNinjaVaultCdnClient, recorder: Recorder, tmp_path: Path
) -> None:
    recorder.responses.append(_file_response)

    target = tmp_path / "logo.png"
    assert await aclient.download_to("assets", "logo.png", target) == 3000
    assert target.read_bytes() == b"abc" * 1000

    sink = io.BytesIO()
    assert await aclient.download_to("assets", "logo.png", sink) == 3000
    assert sink.getvalue() == b"abc" * 1000


class _BrokenAsyncStream(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"partial"
        raise httpx.ReadError("reset")


async def test_download_to_removes_partial_file(
    aclient: AsyncNinjaVaultCdnClient, recorder: Recorder, tmp_path: Path
) -> None:
    recorder.responses.append(httpx.Response(200, stream=_BrokenAsyncStream()))
    target = tmp_path / "partial.bin"

    with pytest.raises(CdnConnectionError):
        await aclient.download_to("assets", "k", target)
    assert not target.exists()


async def test_download_read_wraps_transport_errors(
    aclient: AsyncNinjaVaultCdnClient, recorder: Recorder
) -> None:
    recorder.responses.append(httpx.Response(200, stream=_BrokenAsyncStream()))
    async with aclient.download("assets", "k") as download:
        with pytest.raises(CdnConnectionError):
            await download.read()


def test_public_url_without_http(recorder: Recorder) -> None:
    cdn = AsyncNinjaVaultCdnClient(
        base_url=BASE_URL,
        api_key=API_KEY,
        public_base_url="https://static.test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(recorder)),
    )
    assert (
        cdn.build_public_url("assets", "a b.png") == "https://static.test/public/assets/a%20b.png"
    )
    assert recorder.requests == []
    assert "static" not in repr(cdn)


async def test_lifecycle_and_configuration(
    monkeypatch: pytest.MonkeyPatch, recorder: Recorder
) -> None:
    http = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    async with AsyncNinjaVaultCdnClient(
        base_url=BASE_URL, api_key=API_KEY, http_client=http
    ) as cdn:
        assert cdn.base_url == BASE_URL
    assert not http.is_closed

    with pytest.raises(ValueError, match="event_hooks"):
        AsyncNinjaVaultCdnClient(
            base_url=BASE_URL, api_key=API_KEY, http_client=http, event_hooks={"request": []}
        )
    await http.aclose()

    monkeypatch.setenv("NINJAVAULT_CDN_BASE_URL", "https://env.test")
    monkeypatch.setenv("NINJAVAULT_CDN_API_KEY", "cdn_xxxxx")
    owned = AsyncNinjaVaultCdnClient()
    inner = owned._client
    await owned.close()
    assert inner.is_closed

    monkeypatch.delenv("NINJAVAULT_CDN_API_KEY")
    with pytest.raises(ValueError, match="NINJAVAULT_CDN_API_KEY"):
        AsyncNinjaVaultCdnClient()


async def test_async_event_hooks(monkeypatch: pytest.MonkeyPatch, recorder: Recorder) -> None:
    recorder.responses.append(ok([]))
    real_client = httpx.AsyncClient

    def client_with_mock_transport(**kwargs: object) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(recorder), **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", client_with_mock_transport)

    async def add_correlation_id(request: httpx.Request) -> None:
        request.headers["X-Correlation-Id"] = "corr-2"

    async with AsyncNinjaVaultCdnClient(
        base_url=BASE_URL, api_key=API_KEY, event_hooks={"request": [add_correlation_id]}
    ) as cdn:
        await cdn.list_buckets()

    assert recorder.last.headers["X-Correlation-Id"] == "corr-2"
