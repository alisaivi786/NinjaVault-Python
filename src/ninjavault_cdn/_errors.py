"""Exception types raised by the NinjaVault CDN clients."""

from __future__ import annotations

from collections.abc import Mapping


class NinjaVaultCdnError(Exception):
    """Base class for every error raised by this package."""


class CdnApiError(NinjaVaultCdnError):
    """The CDN answered, but with an error (non-2xx status or a ``success: false`` envelope).

    Attributes:
        status_code: HTTP status code of the response.
        error_code: CDN error code from the envelope (compare with :class:`ErrorCode`), or ``None``
            when the body was not a CDN envelope.
        message: Human-readable error message.
        description: Longer description from the envelope, or the raw (truncated) response body when
            the server did not return a JSON envelope.
        correlation_id: Server trace id (envelope ``traceId``). Log it and send it to the CDN team.
        details: Field-level validation errors (``{"FolderPath": ["..."]}``) or ``None``.
        retry_after: Seconds to wait before retrying, from the ``Retry-After`` header (delta
            seconds or HTTP-date), or ``None``. Set on HTTP 429 / ``ErrorCode.TOO_MANY_REQUESTS``.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        error_code: int | None = None,
        description: str | None = None,
        correlation_id: str | None = None,
        details: Mapping[str, list[str]] | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.description = description
        self.correlation_id = correlation_id
        self.details: dict[str, list[str]] | None = dict(details) if details is not None else None
        self.retry_after = retry_after

    def __str__(self) -> str:
        parts = [f"HTTP {self.status_code}"]
        if self.error_code is not None:
            parts.append(f"error {self.error_code}")
        text = f"{' '.join(parts)}: {self.message}"
        if self.correlation_id:
            text += f" (traceId {self.correlation_id})"
        return text

    def __repr__(self) -> str:
        return (
            f"CdnApiError(status_code={self.status_code!r}, error_code={self.error_code!r}, "
            f"message={self.message!r}, correlation_id={self.correlation_id!r})"
        )


class CdnConnectionError(NinjaVaultCdnError):
    """The request never produced an HTTP response (DNS, TLS, connection reset, ...).

    The original ``httpx`` exception is available as ``__cause__``.
    """


class CdnTimeoutError(CdnConnectionError):
    """The request timed out (connect, read, write or pool timeout)."""
