"""Synchronous NinjaVault CDN client."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from types import TracebackType
from typing import IO, Any

import httpx

from . import _core as core
from ._download import FileDownload
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

EventHooks = Mapping[str, list[Callable[..., Any]]]


class NinjaVaultCdnClient:
    """Server-to-server client for the NinjaVault CDN Server, authenticated with ``X-Api-Key``.

    Never expose the API key to browsers or mobile apps: hand them :meth:`build_public_url`
    (public buckets) or :meth:`create_presigned_url` / :meth:`create_presigned_urls` (private
    files) instead.

    Args:
        base_url: Base URL of the ``/api/v1/...`` routes; a path prefix works
            (``https://gateway.example.com/cdn``). Defaults to ``$NINJAVAULT_CDN_BASE_URL``.
        api_key: The integration's API key. Defaults to ``$NINJAVAULT_CDN_API_KEY``.
        public_base_url: Host for anonymous public files; the client appends ``/public/``.
            Defaults to ``$NINJAVAULT_CDN_PUBLIC_BASE_URL``, then ``base_url``.
        timeout: Timeout in seconds (or an ``httpx.Timeout``) for the client this SDK creates.
            Ignored when ``http_client`` is given.
        http_client: Your own ``httpx.Client`` (proxies, retries via transports, mTLS, ...). The SDK
            never closes a client you pass in.
        event_hooks: ``httpx`` event hooks (``{"request": [...], "response": [...]}``) for your
            own logging or correlation headers. Configure them on ``http_client`` instead if you
            pass one.
        user_agent_suffix: Appended to ``User-Agent``
            (``ninjavault-cdn-python/<version> <suffix>``).

    Raises:
        ValueError: ``base_url`` or ``api_key`` is missing, or a URL is not absolute http(s).
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        public_base_url: str | None = None,
        timeout: float | httpx.Timeout | None = core.DEFAULT_TIMEOUT,
        http_client: httpx.Client | None = None,
        event_hooks: EventHooks | None = None,
        user_agent_suffix: str | None = None,
    ) -> None:
        self._settings = core.resolve_settings(
            base_url, api_key, public_base_url, user_agent_suffix
        )
        if http_client is not None and event_hooks is not None:
            raise ValueError(
                "Pass event_hooks either to NinjaVaultCdnClient or to your own http_client, "
                "not both."
            )
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            timeout=timeout,
            event_hooks={key: list(value) for key, value in (event_hooks or {}).items()},
        )

    # ---------------------------------------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------------------------------------

    @property
    def base_url(self) -> str:
        return self._settings.base_url

    def close(self) -> None:
        """Close the underlying HTTP client if this SDK created it."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> NinjaVaultCdnClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"NinjaVaultCdnClient(base_url={self._settings.base_url!r})"

    # ---------------------------------------------------------------------------------------------
    # Operations
    # ---------------------------------------------------------------------------------------------

    def get_access_context(self) -> AccessContext:
        """Validate the API key and return its tenant/bucket scope. Call once at startup."""
        response, data = self._json("GET", "/api/v1/me")
        return AccessContext.from_dict(core.expect_object(response, data))

    def list_buckets(self) -> list[Bucket]:
        """Buckets visible to the key, with visibility and the caller's usage counters."""
        response, data = self._json("GET", "/api/v1/buckets")
        return [Bucket.from_dict(item) for item in core.expect_list(response, data)]

    def upload(
        self,
        bucket: str,
        tenant_id: int | str,
        file: bytes | bytearray | memoryview | IO[bytes] | Path,
        file_name: str | None = None,
        content_type: str | None = None,
        owner_id: int | None = None,
        folder_path: str | None = None,
    ) -> UploadResult:
        """Upload a file (multipart/form-data).

        ``file`` may be bytes, a binary file object (streamed, not buffered) or a ``pathlib.Path``
        (``file_name`` then defaults to the path's name). When ``content_type`` is omitted it is
        guessed from ``file_name``. ``folder_path`` is up to 8 ``/``-separated segments of
        ``[A-Za-z0-9_-]`` (validated by the server). Persist the returned ``bucket`` and
        ``object_key``.
        """
        parts = core.prepare_upload(
            bucket, tenant_id, file, file_name, content_type, owner_id, folder_path
        )
        try:
            response, data = self._json("POST", "/api/v1/files", data=parts.data, files=parts.files)
        finally:
            parts.close()
        return UploadResult.from_dict(core.expect_object(response, data))

    def list_files(
        self, query: FileListQuery | None = None, **filters: Any
    ) -> PagedResult[FileObject]:
        """List/search file metadata.

        Pass a :class:`FileListQuery`, keyword filters, or both (keywords override the query's
        fields), e.g. ``list_files(bucket="documents", page_size=100)``.
        """
        response, data = self._json("GET", core.list_files_path(query, filters))
        return PagedResult.from_dict(core.expect_object(response, data), FileObject.from_dict)

    def get_metadata(self, bucket: str, object_key: str) -> FileObject:
        """Metadata for one file (checksum, category, thumbnail, ...) without downloading it."""
        path = "/api/v1/file-metadata/" + core.object_path(bucket, object_key)
        response, data = self._json("GET", path)
        return FileObject.from_dict(core.expect_object(response, data))

    def get_summary(self, bucket: str | None = None) -> FileSummary:
        """File count and byte totals per category, across all buckets or for one."""
        response, data = self._json("GET", core.summary_path(bucket))
        return FileSummary.from_dict(core.expect_object(response, data))

    def download(self, bucket: str, object_key: str) -> FileDownload:
        """Open a streamed download. Always use it as a context manager (or call ``close()``)."""
        url = core.api_url(self._settings, "/api/v1/files/" + core.object_path(bucket, object_key))
        request = self._client.build_request(
            "GET", url, headers=core.request_headers(self._settings, accept="*/*")
        )
        response = self._send(request, stream=True)
        if response.is_error:
            try:
                content = response.read()
            finally:
                response.close()
            raise core.http_error(response, content)
        return FileDownload(response)

    def download_to(
        self,
        bucket: str,
        object_key: str,
        destination: str | os.PathLike[str] | IO[bytes],
        *,
        chunk_size: int = core.DOWNLOAD_CHUNK_SIZE,
    ) -> int:
        """Stream a file to a path or binary file object without holding it in memory.

        Returns the number of bytes written. A partially written *path* is removed on failure.
        """
        with self.download(bucket, object_key) as download:
            if isinstance(destination, (str, os.PathLike)):
                target = Path(destination)
                written = 0
                try:
                    with target.open("wb") as handle:
                        for chunk in download.iter_bytes(chunk_size):
                            handle.write(chunk)
                            written += len(chunk)
                except BaseException:
                    target.unlink(missing_ok=True)
                    raise
                return written
            written = 0
            for chunk in download.iter_bytes(chunk_size):
                destination.write(chunk)
                written += len(chunk)
            return written

    def delete(self, bucket: str, object_key: str) -> None:
        """Soft-delete a file."""
        url = core.api_url(self._settings, "/api/v1/files/" + core.object_path(bucket, object_key))
        request = self._client.build_request(
            "DELETE", url, headers=core.request_headers(self._settings)
        )
        response = self._send(request)
        core.check_no_result(response, response.content)

    def create_presigned_url(
        self, bucket: str, object_key: str, expiry_seconds: int | None = None
    ) -> PresignedUrl:
        """Mint one short-lived anonymous download URL for a private file.

        Omit ``expiry_seconds`` for the server default; the server clamps it to its configured max.
        """
        body = core.presign_body(bucket, object_key, expiry_seconds)
        response, data = self._json("POST", "/api/v1/files/presign", json=body)
        return PresignedUrl.from_dict(core.expect_object(response, data))

    def create_presigned_urls(
        self, targets: Iterable[core.PresignTargetLike], expiry_seconds: int | None = None
    ) -> PresignBatchResult:
        """Mint presigned URLs for many files in one call (``(bucket, object_key)`` tuples or
        :class:`PresignTarget`). Partial failure still succeeds: always check ``result.failed``.
        """
        body = core.presign_batch_body(targets, expiry_seconds)
        response, data = self._json("POST", "/api/v1/files/presign/batch", json=body)
        return PresignBatchResult.from_dict(core.expect_object(response, data))

    def build_public_url(self, bucket: str, object_key: str) -> str:
        """Permanent anonymous URL for a file in a *Public* bucket. No HTTP call is made."""
        return core.public_url(self._settings, bucket, object_key)

    # ---------------------------------------------------------------------------------------------
    # Transport
    # ---------------------------------------------------------------------------------------------

    def _json(self, method: str, path: str, **kwargs: Any) -> tuple[httpx.Response, Any]:
        request = self._client.build_request(
            method,
            core.api_url(self._settings, path),
            headers=core.request_headers(self._settings),
            **kwargs,
        )
        response = self._send(request)
        return response, core.envelope_data(response, response.content)

    def _send(self, request: httpx.Request, *, stream: bool = False) -> httpx.Response:
        started = time.perf_counter()
        try:
            response = self._client.send(request, stream=stream)
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
