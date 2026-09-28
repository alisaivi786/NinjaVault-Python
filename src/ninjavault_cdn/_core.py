"""Transport-independent logic shared by the sync and async clients.

Nothing in here performs I/O on the network: it resolves configuration, builds URLs, request bodies
and headers, and turns responses into models or :class:`CdnApiError`.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import IO, Any, Union
from urllib.parse import quote, unquote

import httpx

from ._errors import CdnApiError
from ._models import FileListQuery, PresignTarget
from ._version import __version__

logger = logging.getLogger("ninjavault_cdn")

ENV_BASE_URL = "NINJAVAULT_CDN_BASE_URL"
ENV_API_KEY = "NINJAVAULT_CDN_API_KEY"
ENV_PUBLIC_BASE_URL = "NINJAVAULT_CDN_PUBLIC_BASE_URL"

API_KEY_HEADER = "X-Api-Key"
USER_AGENT = f"ninjavault-cdn-python/{__version__}"
DEFAULT_TIMEOUT = 30.0
DOWNLOAD_CHUNK_SIZE = 64 * 1024
_MAX_RAW_BODY = 4096

#: Anything ``upload`` accepts as file content.
FileContent = Union[bytes, bytearray, memoryview, IO[bytes], Path]
#: A presign batch target: a :class:`PresignTarget` or a ``(bucket, object_key)`` tuple.
PresignTargetLike = Union[PresignTarget, tuple[str, str]]


# --------------------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Settings:
    base_url: str
    api_key: str = field(repr=False)
    public_base_url: str | None
    user_agent: str


def _setting(value: str | None, env_name: str) -> str | None:
    if value is None:
        value = os.environ.get(env_name)
    if value is None or not value.strip():
        return None
    return value.strip()


def _validate_absolute_url(value: str, setting: str) -> str:
    url = httpx.URL(value)
    if url.scheme not in ("http", "https") or not url.host:
        raise ValueError(
            f"NinjaVault CDN {setting} must be an absolute http(s) URL, got {value!r}."
        )
    return value.rstrip("/")


def resolve_settings(
    base_url: str | None,
    api_key: str | None,
    public_base_url: str | None,
    user_agent_suffix: str | None,
) -> Settings:
    """Merge explicit arguments with ``NINJAVAULT_CDN_*`` environment variables; validate them."""
    resolved_base = _setting(base_url, ENV_BASE_URL)
    if resolved_base is None:
        raise ValueError(
            f"NinjaVault CDN base_url is not configured: pass base_url=... or set {ENV_BASE_URL}."
        )
    resolved_key = _setting(api_key, ENV_API_KEY)
    if resolved_key is None:
        raise ValueError(
            f"NinjaVault CDN api_key is not configured: pass api_key=... or set {ENV_API_KEY}."
        )
    resolved_public = _setting(public_base_url, ENV_PUBLIC_BASE_URL)

    user_agent = USER_AGENT
    if user_agent_suffix and user_agent_suffix.strip():
        user_agent = f"{USER_AGENT} {user_agent_suffix.strip()}"

    return Settings(
        base_url=_validate_absolute_url(resolved_base, "base_url"),
        api_key=resolved_key,
        public_base_url=(
            _validate_absolute_url(resolved_public, "public_base_url") if resolved_public else None
        ),
        user_agent=user_agent,
    )


def request_headers(settings: Settings, *, accept: str = "application/json") -> dict[str, str]:
    return {
        API_KEY_HEADER: settings.api_key,
        "User-Agent": settings.user_agent,
        "Accept": accept,
    }


# --------------------------------------------------------------------------------------------------
# URLs
# --------------------------------------------------------------------------------------------------


def require_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string.")
    return value


def encode_segment(value: str) -> str:
    """Percent-encode one path segment exactly like .NET ``Uri.EscapeDataString``."""
    return quote(value, safe="")


def encode_object_key(object_key: str) -> str:
    """Encode each ``/``-separated segment of an object key; empty segments are dropped."""
    require_text(object_key, "object_key")
    segments = [encode_segment(part) for part in object_key.split("/") if part]
    if not segments:
        raise ValueError("object_key must contain at least one non-empty path segment.")
    return "/".join(segments)


def object_path(bucket: str, object_key: str) -> str:
    return f"{encode_segment(require_text(bucket, 'bucket'))}/{encode_object_key(object_key)}"


def build_query(params: Sequence[tuple[str, str]]) -> str:
    if not params:
        return ""
    return "?" + "&".join(
        f"{encode_segment(name)}={encode_segment(value)}" for name, value in params
    )


def api_url(settings: Settings, path: str) -> str:
    """Absolute URL under ``base_url``; keeps any path prefix such as ``https://host/cdn``."""
    return settings.base_url + path


def public_url(settings: Settings, bucket: str, object_key: str) -> str:
    base = settings.public_base_url or settings.base_url
    # The server's own CdnStorage:PublicBaseUrl setting ends in "/public"; accept that value as-is
    # instead of producing ".../public/public/...".
    if base.lower().endswith("/public"):
        base = base[: -len("/public")]
    return f"{base}/public/{object_path(bucket, object_key)}"


def summary_path(bucket: str | None) -> str:
    path = "/api/v1/files/summary"
    if bucket is not None and bucket.strip():
        path += "?bucket=" + encode_segment(bucket)
    return path


def list_files_path(query: FileListQuery | None, filters: Mapping[str, Any]) -> str:
    if filters:
        query = replace(query or FileListQuery(), **filters)
    return "/api/v1/files" + (build_query(query.to_params()) if query is not None else "")


# --------------------------------------------------------------------------------------------------
# Request bodies
# --------------------------------------------------------------------------------------------------


@dataclass(slots=True)
class UploadParts:
    data: dict[str, str]
    files: dict[str, Any]
    opened: IO[bytes] | None = None

    def close(self) -> None:
        if self.opened is not None:
            self.opened.close()


def prepare_upload(
    bucket: str,
    tenant_id: int | str,
    file: FileContent,
    file_name: str | None,
    content_type: str | None,
    owner_id: int | None,
    folder_path: str | None,
) -> UploadParts:
    require_text(bucket, "bucket")
    if isinstance(tenant_id, bool) or not isinstance(tenant_id, (int, str)):
        raise TypeError("tenant_id must be an int or a str.")
    tenant_text = str(tenant_id)
    require_text(tenant_text, "tenant_id")

    opened: IO[bytes] | None = None
    content: bytes | IO[bytes]
    if isinstance(file, Path):
        if file_name is None:
            file_name = file.name
        opened = file.open("rb")
        content = opened
    elif isinstance(file, (bytes, bytearray, memoryview)):
        content = bytes(file)
    elif hasattr(file, "read"):
        content = file
    else:
        raise TypeError("file must be bytes, a binary file object, or a pathlib.Path.")

    if file_name is None or not file_name.strip():
        if opened is not None:
            opened.close()
        raise ValueError("file_name must be a non-empty string.")

    data = {"Bucket": bucket, "TenantId": tenant_text}
    if owner_id is not None:
        data["OwnerId"] = str(owner_id)
    if folder_path is not None and folder_path.strip():
        data["FolderPath"] = folder_path

    part: tuple[Any, ...]
    if content_type is not None and content_type.strip():
        part = (file_name, content, content_type.strip())
    else:
        # httpx guesses the part's Content-Type from the file name (octet-stream as a last resort).
        part = (file_name, content)
    return UploadParts(data=data, files={"File": part}, opened=opened)


def presign_body(bucket: str, object_key: str, expiry_seconds: int | None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "bucket": require_text(bucket, "bucket"),
        "objectKey": require_text(object_key, "object_key"),
    }
    if expiry_seconds is not None:
        body["expirySeconds"] = expiry_seconds
    return body


def presign_batch_body(
    targets: Iterable[PresignTargetLike], expiry_seconds: int | None
) -> dict[str, Any]:
    items: list[dict[str, str]] = []
    for item in targets:
        target = item if isinstance(item, PresignTarget) else PresignTarget(*item)
        require_text(target.bucket, "bucket")
        require_text(target.object_key, "object_key")
        items.append(target.to_dict())
    body: dict[str, Any] = {"targets": items}
    if expiry_seconds is not None:
        body["expirySeconds"] = expiry_seconds
    return body


# --------------------------------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------------------------------


def _ci_get(data: Mapping[str, Any], name: str) -> Any:
    folded = name.casefold()
    for key, value in data.items():
        if str(key).casefold() == folded:
            return value
    return None


def retry_after_seconds(headers: httpx.Headers) -> float | None:
    """``Retry-After`` as seconds: delta-seconds or an HTTP-date (clamped to >= 0)."""
    value = headers.get("Retry-After")
    if value is None or not value.strip():
        return None
    value = value.strip()
    try:
        return max(0.0, float(int(value)))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def _details(value: Any) -> dict[str, list[str]] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, list[str]] = {}
    for key, errors in value.items():
        if isinstance(errors, list):
            result[str(key)] = [str(item) for item in errors]
        elif errors is not None:
            result[str(key)] = [str(errors)]
    return result


def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _raw_text(content: bytes) -> str | None:
    text = content.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    return text if len(text) <= _MAX_RAW_BODY else text[:_MAX_RAW_BODY] + "..."


def error_from_envelope(
    response: httpx.Response,
    error: Any,
    *,
    fallback_message: str,
    raw_body: str | None = None,
) -> CdnApiError:
    if isinstance(error, Mapping):
        message = _ci_get(error, "message")
        description = _ci_get(error, "description")
        trace_id = _ci_get(error, "traceId")
        return CdnApiError(
            str(message) if message else fallback_message,
            status_code=response.status_code,
            error_code=_int_or_none(_ci_get(error, "errorCode")),
            description=str(description) if description is not None else None,
            correlation_id=str(trace_id) if trace_id else None,
            details=_details(_ci_get(error, "details")),
            retry_after=retry_after_seconds(response.headers),
        )
    return CdnApiError(
        fallback_message,
        status_code=response.status_code,
        description=raw_body,
        retry_after=retry_after_seconds(response.headers),
    )


def http_error(response: httpx.Response, content: bytes) -> CdnApiError:
    """Build the error for a non-2xx response; the body may be an envelope or anything else."""
    fallback = f"CDN request failed with HTTP {response.status_code}."
    error: Any = None
    if content.strip():
        try:
            payload = json.loads(content)
        except ValueError:
            payload = None
        if isinstance(payload, Mapping):
            error = _ci_get(payload, "error")
    return error_from_envelope(
        response, error, fallback_message=fallback, raw_body=_raw_text(content)
    )


def envelope_data(response: httpx.Response, content: bytes) -> Any:
    """Return the envelope's ``data`` , raising :class:`CdnApiError` on any failure."""
    if response.is_error:
        raise http_error(response, content)
    if not content.strip():
        raise CdnApiError("CDN response body was empty.", status_code=response.status_code)
    try:
        payload = json.loads(content)
    except ValueError:
        raise CdnApiError(
            "CDN response was not valid JSON.",
            status_code=response.status_code,
            description=_raw_text(content),
        ) from None
    if not isinstance(payload, Mapping):
        raise CdnApiError(
            "CDN response was not a JSON envelope.",
            status_code=response.status_code,
            description=_raw_text(content),
        )
    if _ci_get(payload, "success") is not True:
        raise error_from_envelope(
            response, _ci_get(payload, "error"), fallback_message="CDN request failed."
        )
    return _ci_get(payload, "data")


def expect_object(response: httpx.Response, data: Any) -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        raise CdnApiError("CDN response data was not an object.", status_code=response.status_code)
    return data


def expect_list(response: httpx.Response, data: Any) -> list[Mapping[str, Any]]:
    if not isinstance(data, list):
        raise CdnApiError("CDN response data was not a list.", status_code=response.status_code)
    return [item for item in data if isinstance(item, Mapping)]


def check_no_result(response: httpx.Response, content: bytes) -> None:
    """For routes without a result (delete): 204/empty body or a success envelope are both fine."""
    if response.is_error:
        raise http_error(response, content)
    if not content.strip():
        return
    try:
        payload = json.loads(content)
    except ValueError:
        return
    if isinstance(payload, Mapping) and _ci_get(payload, "success") is False:
        raise error_from_envelope(
            response, _ci_get(payload, "error"), fallback_message="CDN request failed."
        )


# --------------------------------------------------------------------------------------------------
# Downloads
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DownloadHeaders:
    content_type: str | None
    file_name: str | None
    size_bytes: int | None
    etag: str | None


_FILENAME_STAR_RE = re.compile(
    r"filename\*\s*=\s*(?P<charset>[^']*)'[^']*'(?P<value>[^;]+)", re.IGNORECASE
)


def content_disposition_file_name(disposition: str) -> str | None:
    """File name from ``Content-Disposition``, preferring RFC 5987 ``filename*`` (like .NET)."""
    star = _FILENAME_STAR_RE.search(disposition)
    if star is not None:
        charset = star.group("charset").strip() or "utf-8"
        value = star.group("value").strip().strip('"')
        try:
            return unquote(value, encoding=charset, errors="strict")
        except (LookupError, UnicodeDecodeError):
            pass
    message = Message()
    message["Content-Disposition"] = disposition
    return message.get_filename()


def download_headers(response: httpx.Response) -> DownloadHeaders:
    headers = response.headers
    media_type: str | None = None
    content_type = headers.get("Content-Type")
    if content_type:
        media_type = content_type.split(";", 1)[0].strip() or None

    disposition = headers.get("Content-Disposition")
    file_name = content_disposition_file_name(disposition) if disposition else None

    length = headers.get("Content-Length")
    size = int(length) if length is not None and length.strip().isdigit() else None
    return DownloadHeaders(media_type, file_name, size, headers.get("ETag"))


def log_response(method: str, url: httpx.URL, status: int, elapsed_ms: float) -> None:
    # URL only: the API key travels in a header and is never logged, nor are any bodies.
    logger.debug("%s %s -> %d (%.1f ms)", method, url, status, elapsed_ms)


def log_failure(method: str, url: httpx.URL, exc: Exception, elapsed_ms: float) -> None:
    logger.debug("%s %s failed after %.1f ms: %s", method, url, elapsed_ms, type(exc).__name__)
