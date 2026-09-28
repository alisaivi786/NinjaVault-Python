"""Asynchronous NinjaVault CDN client (same surface as :class:`NinjaVaultCdnClient`)."""

from __future__ import annotations

import os
import time
from collections.abc import Awaitable, Callable, Coroutine, Generator, Iterable, Mapping
from pathlib import Path
from types import TracebackType
from typing import IO, Any

import httpx

from . import _core as core
from ._download import AsyncFileDownload
from ._errors import CdnConnectionError, CdnTimeoutError
from ._models import (
    AccessContext,
    Bucket,
    FileListQuery,
    FileObject,
    FileSummary,
    PagedResult,
    PresignBatchResult,
    PresignedUrl,
    UploadResult,
)

AsyncEventHooks = Mapping[str, list[Callable[..., Awaitable[Any]]]]


class _DownloadContext:
    """Lets ``download()`` be awaited *or* used directly with ``async with``."""

    __slots__ = ("_coro", "_download")

    def __init__(self, coro: Coroutine[Any, Any, AsyncFileDownload]) -> None:
        self._coro = coro
        self._download: AsyncFileDownload | None = None

    def __await__(self) -> Generator[Any, None, AsyncFileDownload]:
        return self._coro.__await__()

    async def __aenter__(self) -> AsyncFileDownload:
        self._download = await self._coro
        return self._download

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._download is not None:
            await self._download.aclose()


class AsyncNinjaVaultCdnClient:
    """Async server-to-server client for the NinjaVault CDN Server (``httpx.AsyncClient``).

    Arguments are identical to :class:`NinjaVaultCdnClient`, except ``http_client`` must be an
    ``httpx.AsyncClient`` and ``event_hooks`` must be async callables. Use ``async with`` or
    ``await client.aclose()``.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        public_base_url: str | None = None,
        timeout: float | httpx.Timeout | None = core.DEFAULT_TIMEOUT,
        http_client: httpx.AsyncClient | None = None,
        event_hooks: AsyncEventHooks | None = None,
        user_agent_suffix: str | None = None,
    ) -> None:
        self._settings = core.resolve_settings(
            base_url, api_key, public_base_url, user_agent_suffix
        )
        if http_client is not None and event_hooks is not None:
            raise ValueError(
                "Pass event_hooks either to AsyncNinjaVaultCdnClient or to your own http_client, "
                "not both."
            )
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            timeout=timeout,
            event_hooks={key: list(value) for key, value in (event_hooks or {}).items()},
        )

    # ---------------------------------------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------------------------------------

    @property
    def base_url(self) -> str:
        return self._settings.base_url

    async def aclose(self) -> None:
        """Close the underlying HTTP client if this SDK created it."""
        if self._owns_client:
            await self._client.aclose()

    async def close(self) -> None:
        """Alias of :meth:`aclose`."""
        await self.aclose()

    async def __aenter__(self) -> AsyncNinjaVaultCdnClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    def __repr__(self) -> str:
        return f"AsyncNinjaVaultCdnClient(base_url={self._settings.base_url!r})"

    # ---------------------------------------------------------------------------------------------
    # Operations
    # ---------------------------------------------------------------------------------------------

    async def get_access_context(self) -> AccessContext:
        """Validate the API key and return its tenant/bucket scope. Call once at startup."""
        response, data = await self._json("GET", "/api/v1/me")
        return AccessContext.from_dict(core.expect_object(response, data))

    async def list_buckets(self) -> list[Bucket]:
        """Buckets visible to the key, with visibility and the caller's usage counters."""
        response, data = await self._json("GET", "/api/v1/buckets")
        return [Bucket.from_dict(item) for item in core.expect_list(response, data)]

    async def upload(
        self,
        bucket: str,
        tenant_id: int | str,
        file: bytes | bytearray | memoryview | IO[bytes] | Path,
        file_name: str | None = None,
        content_type: str | None = None,
        owner_id: int | None = None,
        folder_path: str | None = None,
    ) -> UploadResult:
        """Upload a file (multipart/form-data). See :meth:`NinjaVaultCdnClient.upload`."""
        parts = core.prepare_upload(
            bucket, tenant_id, file, file_name, content_type, owner_id, folder_path
        )
        try:
            response, data = await self._json(
                "POST", "/api/v1/files", data=parts.data, files=parts.files
            )
        finally:
            parts.close()
        return UploadResult.from_dict(core.expect_object(response, data))

    async def list_files(
        self, query: FileListQuery | None = None, **filters: Any
    ) -> PagedResult[FileObject]:
        """List/search file metadata. See :meth:`NinjaVaultCdnClient.list_files`."""
        response, data = await self._json("GET", core.list_files_path(query, filters))
        return PagedResult.from_dict(core.expect_object(response, data), FileObject.from_dict)

    async def get_metadata(self, bucket: str, object_key: str) -> FileObject:
        """Metadata for one file without downloading it."""
        path = "/api/v1/file-metadata/" + core.object_path(bucket, object_key)
        response, data = await self._json("GET", path)
        return FileObject.from_dict(core.expect_object(response, data))

    async def get_summary(self, bucket: str | None = None) -> FileSummary:
        """File count and byte totals per category, across all buckets or for one."""
        response, data = await self._json("GET", core.summary_path(bucket))
        return FileSummary.from_dict(core.expect_object(response, data))

    def download(self, bucket: str, object_key: str) -> _DownloadContext:
        """Open a streamed download.

        Use ``async with client.download(...) as download:`` (closes automatically), or
        ``download = await client.download(...)`` and ``await download.aclose()`` yourself.
        """
        # Validate eagerly so bad arguments raise at the call site, not on first await.
        path = "/api/v1/files/" + core.object_path(bucket, object_key)
        return _DownloadContext(self._open_download(path))

    async def _open_download(self, path: str) -> AsyncFileDownload:
        request = self._client.build_request(
            "GET",
            core.api_url(self._settings, path),
            headers=core.request_headers(self._settings, accept="*/*"),
        )
        response = await self._send(request, stream=True)
        if response.is_error:
            try:
                content = await response.aread()
            finally:
                await response.aclose()
            raise core.http_error(response, content)
        return AsyncFileDownload(response)

    async def download_to(
        self,
        bucket: str,
        object_key: str,
        destination: str | os.PathLike[str] | IO[bytes],
        *,
        chunk_size: int = core.DOWNLOAD_CHUNK_SIZE,
    ) -> int:
        """Stream a file to a path or binary file object without holding it in memory.

        Returns the number of bytes written. A partially written *path* is removed on failure.
        Local file writes are ordinary (blocking) writes of ``chunk_size`` bytes.
        """
        async with self.download(bucket, object_key) as download:
            if isinstance(destination, (str, os.PathLike)):
                target = Path(destination)
                written = 0
                try:
                    with target.open("wb") as handle:
                        async for chunk in download.aiter_bytes(chunk_size):
                            handle.write(chunk)
                            written += len(chunk)
                except BaseException:
                    target.unlink(missing_ok=True)
                    raise
                return written
            written = 0
            async for chunk in download.aiter_bytes(chunk_size):
                destination.write(chunk)
                written += len(chunk)
            return written

    async def delete(self, bucket: str, object_key: str) -> None:
        """Soft-delete a file."""
        url = core.api_url(self._settings, "/api/v1/files/" + core.object_path(bucket, object_key))
        request = self._client.build_request(
            "DELETE", url, headers=core.request_headers(self._settings)
        )
        response = await self._send(request)
        core.check_no_result(response, response.content)

    async def create_presigned_url(
        self, bucket: str, object_key: str, expiry_seconds: int | None = None
    ) -> PresignedUrl:
        """Mint one short-lived anonymous download URL for a private file."""
        body = core.presign_body(bucket, object_key, expiry_seconds)
        response, data = await self._json("POST", "/api/v1/files/presign", json=body)
        return PresignedUrl.from_dict(core.expect_object(response, data))

    async def create_presigned_urls(
        self, targets: Iterable[core.PresignTargetLike], expiry_seconds: int | None = None
    ) -> PresignBatchResult:
        """Mint presigned URLs for many files in one call. Always check ``result.failed``."""
        body = core.presign_batch_body(targets, expiry_seconds)
        response, data = await self._json("POST", "/api/v1/files/presign/batch", json=body)
        return PresignBatchResult.from_dict(core.expect_object(response, data))

    def build_public_url(self, bucket: str, object_key: str) -> str:
        """Permanent anonymous URL for a file in a *Public* bucket. No HTTP call is made."""
        return core.public_url(self._settings, bucket, object_key)

    # ---------------------------------------------------------------------------------------------
    # Transport
    # ---------------------------------------------------------------------------------------------

    async def _json(self, method: str, path: str, **kwargs: Any) -> tuple[httpx.Response, Any]:
        request = self._client.build_request(
            method,
            core.api_url(self._settings, path),
            headers=core.request_headers(self._settings),
            **kwargs,
        )
        response = await self._send(request)
        return response, core.envelope_data(response, response.content)

    async def _send(self, request: httpx.Request, *, stream: bool = False) -> httpx.Response:
        started = time.perf_counter()
        try:
            response = await self._client.send(request, stream=stream)
        except httpx.TimeoutException as exc:
            core.log_failure(request.method, request.url, exc, _elapsed_ms(started))
            raise CdnTimeoutError(f"CDN request timed out: {request.method} {request.url}") from exc
        except httpx.TransportError as exc:
            core.log_failure(request.method, request.url, exc, _elapsed_ms(started))
            raise CdnConnectionError(
                f"CDN request failed: {request.method} {request.url}: {exc}"
            ) from exc
        core.log_response(request.method, request.url, response.status_code, _elapsed_ms(started))
        return response


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0
