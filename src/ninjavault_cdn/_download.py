"""Streaming download handles returned by ``download()``."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from types import TracebackType

import httpx

from ._core import DOWNLOAD_CHUNK_SIZE, download_headers
from ._errors import CdnConnectionError, CdnTimeoutError


def _wrap_transport_error(exc: httpx.TransportError) -> CdnConnectionError:
    if isinstance(exc, httpx.TimeoutException):
        return CdnTimeoutError(f"CDN download timed out: {exc}")
    return CdnConnectionError(f"CDN download failed: {exc}")


class _DownloadBase:
    __slots__ = ("_response", "content_type", "etag", "file_name", "size_bytes")

    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        headers = download_headers(response)
        #: Media type of the file (``Content-Type`` without parameters), if the server sent one.
        self.content_type: str | None = headers.content_type
        #: Original file name from ``Content-Disposition`` (``filename*`` preferred), if any.
        self.file_name: str | None = headers.file_name
        #: Size from ``Content-Length``; ``None`` when the response is chunked.
        self.size_bytes: int | None = headers.size_bytes
        #: Quoted SHA-256 checksum of the stored bytes, as served in the ``ETag`` header.
        self.etag: str | None = headers.etag

    @property
    def response(self) -> httpx.Response:
        """The underlying streamed ``httpx.Response`` (headers, status, ...)."""
        return self._response

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(file_name={self.file_name!r}, "
            f"content_type={self.content_type!r}, size_bytes={self.size_bytes!r})"
        )


class FileDownload(_DownloadBase):
    """A streamed file download. Use it as a context manager so the connection is always released.

    >>> with cdn.download("documents", key) as download:
    ...     for chunk in download.iter_bytes():
    ...         sink.write(chunk)
    """

    __slots__ = ()

    def iter_bytes(self, chunk_size: int | None = DOWNLOAD_CHUNK_SIZE) -> Iterator[bytes]:
        """Yield the body in chunks without loading the whole file into memory."""
        try:
            yield from self._response.iter_bytes(chunk_size)
        except httpx.TransportError as exc:
            raise _wrap_transport_error(exc) from exc

    def read(self) -> bytes:
        """Read the entire body into memory. Prefer :meth:`iter_bytes` for large files."""
        try:
            return self._response.read()
        except httpx.TransportError as exc:
            raise _wrap_transport_error(exc) from exc

    def close(self) -> None:
        """Release the HTTP connection. Safe to call more than once."""
        self._response.close()

    def __enter__(self) -> FileDownload:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class AsyncFileDownload(_DownloadBase):
    """A streamed file download for the async client. Use ``async with``.

    >>> async with await cdn.download("documents", key) as download:
    ...     async for chunk in download.aiter_bytes():
    ...         await sink.write(chunk)
    """

    __slots__ = ()

    async def aiter_bytes(
        self, chunk_size: int | None = DOWNLOAD_CHUNK_SIZE
    ) -> AsyncIterator[bytes]:
        """Yield the body in chunks without loading the whole file into memory."""
        try:
            async for chunk in self._response.aiter_bytes(chunk_size):
                yield chunk
        except httpx.TransportError as exc:
            raise _wrap_transport_error(exc) from exc

    async def read(self) -> bytes:
        """Read the entire body into memory. Prefer :meth:`aiter_bytes` for large files."""
        try:
            return await self._response.aread()
        except httpx.TransportError as exc:
            raise _wrap_transport_error(exc) from exc

    async def aclose(self) -> None:
        """Release the HTTP connection. Safe to call more than once."""
        await self._response.aclose()

    async def __aenter__(self) -> AsyncFileDownload:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
