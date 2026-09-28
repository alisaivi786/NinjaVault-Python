"""Immutable request/response models for the NinjaVault CDN API.

Every response model has a ``from_dict`` constructor that accepts the server's camelCase JSON. Field
lookup is case-insensitive, unknown extra fields are ignored, and missing fields fall back to empty
defaults, so a newer server never breaks an older SDK.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Generic, TypeVar

from ._enums import (
    BucketVisibility,
    FileCategory,
    FileSortBy,
    PresignFailureReason,
    ThumbnailStatus,
    enum_to_wire,
    parse_enum,
)

__all__ = [
    "AccessBucket",
    "AccessContext",
    "Bucket",
    "FileCategorySummary",
    "FileListQuery",
    "FileObject",
    "FileSummary",
    "PagedResult",
    "PresignBatchResult",
    "PresignFailure",
    "PresignTarget",
    "PresignedTarget",
    "PresignedUrl",
    "UploadResult",
]

T = TypeVar("T")

#: Returned for a timestamp the server omitted (mirrors ``DateTimeOffset.MinValue`` in .NET).
MIN_DATETIME = datetime(1, 1, 1, tzinfo=timezone.utc)

_ISO_RE = re.compile(
    r"^(?P<main>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?)"
    r"(?:\.(?P<frac>\d+))?"
    r"(?P<tz>Z|z|[+-]\d{2}:?\d{2})?$"
)


def parse_datetime(value: object) -> datetime | None:
    """Parse an ISO-8601 timestamp into a timezone-aware ``datetime`` (naive values are UTC).

    Handles what .NET emits and Python 3.10's ``fromisoformat`` rejects: a ``Z`` suffix and 7-digit
    fractional seconds.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    match = _ISO_RE.match(text)
    if match is None:
        return None
    normalized = match.group("main").replace(" ", "T")
    fraction = match.group("frac")
    if fraction:
        normalized += "." + fraction[:6].ljust(6, "0")
    tz = match.group("tz")
    if tz is None or tz in ("Z", "z"):
        normalized += "+00:00"
    else:
        normalized += tz if ":" in tz else f"{tz[:3]}:{tz[3:]}"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def format_datetime(value: datetime) -> str:
    """Format a ``datetime`` as ISO-8601 UTC with a ``Z`` suffix (naive values are UTC)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def to_tenant_id(value: object) -> int | str:
    """Tenant ids are strings on the wire: a positive integer (``"42"``) or a GUID.

    Numeric ids (JSON numbers or numeric strings) become ``int``; GUIDs stay ``str``.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.isascii() and text.isdigit():
        return int(text)
    return text


class _Fields:
    """Case-insensitive, forgiving accessor over one JSON object."""

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, Any]) -> None:
        self._data = {str(key).casefold(): value for key, value in data.items()}

    def raw(self, name: str) -> Any:
        return self._data.get(name.casefold())

    def text(self, name: str) -> str:
        value = self.raw(name)
        return "" if value is None else str(value)

    def opt_text(self, name: str) -> str | None:
        value = self.raw(name)
        return None if value is None else str(value)

    def opt_int(self, name: str) -> int | None:
        value = self.raw(name)
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        try:
            return int(float(value)) if isinstance(value, float) else int(str(value))
        except ValueError:
            return None

    def integer(self, name: str) -> int:
        value = self.opt_int(name)
        return 0 if value is None else value

    def boolean(self, name: str) -> bool:
        value = self.raw(name)
        if isinstance(value, str):
            return value.strip().lower() == "true"
        return bool(value)

    def opt_datetime(self, name: str) -> datetime | None:
        return parse_datetime(self.raw(name))

    def date_time(self, name: str) -> datetime:
        value = self.opt_datetime(name)
        return MIN_DATETIME if value is None else value

    def objects(self, name: str) -> list[Mapping[str, Any]]:
        value = self.raw(name)
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, Mapping)]

    def tenant_id(self, name: str) -> int | str:
        return to_tenant_id(self.raw(name))

    def tenant_ids(self, name: str) -> tuple[int | str, ...]:
        value = self.raw(name)
        if not isinstance(value, list):
            return ()
        return tuple(to_tenant_id(item) for item in value if item is not None)

    def texts(self, name: str) -> tuple[str, ...]:
        value = self.raw(name)
        if not isinstance(value, list):
            return ()
        return tuple(str(item) for item in value if item is not None)


# --------------------------------------------------------------------------------------------------
# Identity and buckets
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AccessBucket:
    """One bucket in the key's catalogue (``GET /api/v1/me``)."""

    name: str
    visibility: BucketVisibility | str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AccessBucket:
        f = _Fields(data)
        return cls(
            name=f.text("name"), visibility=parse_enum(BucketVisibility, f.raw("visibility"))
        )


@dataclass(frozen=True, slots=True)
class AccessContext:
    """What the configured API key is and what it may reach (``GET /api/v1/me``).

    Empty ``allowed_buckets`` / ``allowed_tenant_ids`` mean *unrestricted*, not "no access".
    Tenant ids are strings on the wire (``["1"]``); numeric ones are exposed as ``int`` and GUID
    tenant ids stay ``str``.
    """

    name: str
    is_unrestricted: bool
    is_admin: bool
    allowed_buckets: tuple[str, ...]
    allowed_tenant_ids: tuple[int | str, ...]
    buckets: tuple[AccessBucket, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AccessContext:
        f = _Fields(data)
        return cls(
            name=f.text("name"),
            is_unrestricted=f.boolean("isUnrestricted"),
            is_admin=f.boolean("isAdmin"),
            allowed_buckets=f.texts("allowedBuckets"),
            allowed_tenant_ids=f.tenant_ids("allowedTenantIds"),
            buckets=tuple(AccessBucket.from_dict(item) for item in f.objects("buckets")),
        )


@dataclass(frozen=True, slots=True)
class Bucket:
    """A configured bucket with the caller's file count and byte total (``GET /api/v1/buckets``)."""

    name: str
    visibility: BucketVisibility | str
    file_count: int
    total_size_bytes: int

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Bucket:
        f = _Fields(data)
        return cls(
            name=f.text("name"),
            visibility=parse_enum(BucketVisibility, f.raw("visibility")),
            file_count=f.integer("fileCount"),
            total_size_bytes=f.integer("totalSizeBytes"),
        )


# --------------------------------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UploadResult:
    """Result of ``POST /api/v1/files``.

    Persist ``bucket`` + ``object_key``: the object key (not the original file name) identifies the
    file from now on. This shape is intentionally smaller than :class:`FileObject`; call
    ``get_metadata`` for checksum, category and thumbnail details.
    """

    id: str
    bucket: str
    object_key: str
    original_file_name: str
    content_type: str
    size_bytes: int
    visibility: BucketVisibility | str
    url: str
    view_url: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> UploadResult:
        f = _Fields(data)
        return cls(
            id=f.text("id"),
            bucket=f.text("bucket"),
            object_key=f.text("objectKey"),
            original_file_name=f.text("originalFileName"),
            content_type=f.text("contentType"),
            size_bytes=f.integer("sizeBytes"),
            visibility=parse_enum(BucketVisibility, f.raw("visibility")),
            url=f.text("url"),
            view_url=f.opt_text("viewUrl"),
        )


@dataclass(frozen=True, slots=True)
class FileObject:
    """Full file metadata as returned by the list and metadata routes.

    ``url`` is the permanent public URL for public buckets, or the API-key-gated download route for
    private ones (a browser cannot call that; mint a presigned URL instead). ``view_url`` is set for
    public files only and renders inline (``?disposition=inline``).
    """

    id: str
    tenant_id: int | str
    owner_id: int | None
    bucket: str
    object_key: str
    original_file_name: str
    content_type: str
    size_bytes: int
    checksum: str | None
    visibility: BucketVisibility | str
    extension: str | None
    category: FileCategory | str
    thumbnail_status: ThumbnailStatus | str
    thumbnail_url: str | None
    created_at_utc: datetime
    url: str
    view_url: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FileObject:
        f = _Fields(data)
        return cls(
            id=f.text("id"),
            tenant_id=f.tenant_id("tenantId"),
            owner_id=f.opt_int("ownerId"),
            bucket=f.text("bucket"),
            object_key=f.text("objectKey"),
            original_file_name=f.text("originalFileName"),
            content_type=f.text("contentType"),
            size_bytes=f.integer("sizeBytes"),
            checksum=f.opt_text("checksum"),
            visibility=parse_enum(BucketVisibility, f.raw("visibility")),
            extension=f.opt_text("extension"),
            category=parse_enum(FileCategory, f.raw("category")),
            thumbnail_status=parse_enum(ThumbnailStatus, f.raw("thumbnailStatus")),
            thumbnail_url=f.opt_text("thumbnailUrl"),
            # The server names this "createdAtUTC"; lookup is case-insensitive.
            created_at_utc=f.date_time("createdAtUtc"),
            url=f.text("url"),
            view_url=f.opt_text("viewUrl"),
        )


@dataclass(frozen=True, slots=True)
class PagedResult(Generic[T]):
    """One page of results."""

    items: tuple[T, ...]
    total_count: int
    page: int
    page_size: int

    @classmethod
    def from_dict(
        cls, data: Mapping[str, Any], item_factory: Callable[[Mapping[str, Any]], T]
    ) -> PagedResult[T]:
        f = _Fields(data)
        return cls(
            items=tuple(item_factory(item) for item in f.objects("items")),
            total_count=f.integer("totalCount"),
            page=f.integer("page"),
            page_size=f.integer("pageSize"),
        )


@dataclass(frozen=True, slots=True)
class FileCategorySummary:
    """One category's share of :class:`FileSummary`."""

    category: FileCategory | str
    file_count: int
    total_size_bytes: int

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FileCategorySummary:
        f = _Fields(data)
        return cls(
            category=parse_enum(FileCategory, f.raw("category")),
            file_count=f.integer("fileCount"),
            total_size_bytes=f.integer("totalSizeBytes"),
        )


@dataclass(frozen=True, slots=True)
class FileSummary:
    """Storage totals with a per-category breakdown (``GET /api/v1/files/summary``)."""

    total_file_count: int
    total_size_bytes: int
    categories: tuple[FileCategorySummary, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FileSummary:
        f = _Fields(data)
        return cls(
            total_file_count=f.integer("totalFileCount"),
            total_size_bytes=f.integer("totalSizeBytes"),
            categories=tuple(
                FileCategorySummary.from_dict(item) for item in f.objects("categories")
            ),
        )


# --------------------------------------------------------------------------------------------------
# Presigned URLs
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PresignedUrl:
    """A short-lived anonymous download URL for a private file."""

    url: str
    expires_at_utc: datetime

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PresignedUrl:
        f = _Fields(data)
        return cls(url=f.text("url"), expires_at_utc=f.date_time("expiresAtUtc"))


@dataclass(frozen=True, slots=True)
class PresignTarget:
    """One file to presign in a batch request."""

    bucket: str
    object_key: str

    def to_dict(self) -> dict[str, str]:
        return {"bucket": self.bucket, "objectKey": self.object_key}


@dataclass(frozen=True, slots=True)
class PresignedTarget:
    """A presigned URL minted by a batch request, with the bucket/key it belongs to."""

    bucket: str
    object_key: str
    url: str
    expires_at_utc: datetime

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PresignedTarget:
        f = _Fields(data)
        return cls(
            bucket=f.text("bucket"),
            object_key=f.text("objectKey"),
            url=f.text("url"),
            expires_at_utc=f.date_time("expiresAtUtc"),
        )


@dataclass(frozen=True, slots=True)
class PresignFailure:
    """A batch target that could not be presigned."""

    bucket: str
    object_key: str
    reason: PresignFailureReason | str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PresignFailure:
        f = _Fields(data)
        return cls(
            bucket=f.text("bucket"),
            object_key=f.text("objectKey"),
            reason=parse_enum(PresignFailureReason, f.raw("reason")),
        )


@dataclass(frozen=True, slots=True)
class PresignBatchResult:
    """Outcome of a batch presign.

    The server answers 200 even when some targets fail: always check ``failed``.
    """

    succeeded: tuple[PresignedTarget, ...]
    failed: tuple[PresignFailure, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PresignBatchResult:
        f = _Fields(data)
        return cls(
            succeeded=tuple(PresignedTarget.from_dict(item) for item in f.objects("succeeded")),
            failed=tuple(PresignFailure.from_dict(item) for item in f.objects("failed")),
        )


# --------------------------------------------------------------------------------------------------
# Queries
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FileListQuery:
    """Filters, sorting and paging for ``list_files``. Every field is optional.

    Server defaults when omitted: ``sort_by=CREATED_AT_UTC``, ``sort_descending=True``, ``page=1``,
    ``page_size=50`` (max 200). Naive datetimes are treated as UTC.
    """

    bucket: str | None = None
    tenant_id: int | str | None = None
    owner_id: int | None = None
    object_key_prefix: str | None = None
    file_name_contains: str | None = None
    content_type: str | None = None
    category: FileCategory | str | None = None
    visibility: BucketVisibility | str | None = None
    created_from_utc: datetime | None = None
    created_to_utc: datetime | None = None
    include_deleted: bool | None = None
    sort_by: FileSortBy | str | None = None
    sort_descending: bool | None = None
    page: int | None = None
    page_size: int | None = None

    def to_params(self) -> list[tuple[str, str]]:
        """Query parameters with the server's names, in a stable order; blanks are skipped."""
        pairs: Iterable[tuple[str, object]] = (
            ("bucket", self.bucket),
            ("tenantId", self.tenant_id),
            ("ownerId", self.owner_id),
            ("objectKeyPrefix", self.object_key_prefix),
            ("fileNameContains", self.file_name_contains),
            ("contentType", self.content_type),
            ("category", self.category),
            ("visibility", self.visibility),
            ("createdFromUTC", self.created_from_utc),
            ("createdToUTC", self.created_to_utc),
            ("includeDeleted", self.include_deleted),
            ("sortBy", self.sort_by),
            ("sortDescending", self.sort_descending),
            ("page", self.page),
            ("pageSize", self.page_size),
        )
        params: list[tuple[str, str]] = []
        for name, value in pairs:
            if value is None:
                continue
            if isinstance(value, bool):
                text = "true" if value else "false"
            elif isinstance(value, datetime):
                text = format_datetime(value)
            else:
                text = enum_to_wire(value)
            if text.strip():
                params.append((name, text))
        return params
