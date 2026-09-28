"""Enumerations used on the NinjaVault CDN wire contract.

The server serializes enums as their member *names* (``JsonStringEnumConverter``), so every member's
value here is the exact string the server sends and expects. Response parsing never fails on an enum
value the SDK does not know yet: unknown values are kept as the raw string (see ``parse_enum``).
"""

from __future__ import annotations

from enum import Enum, IntEnum
from typing import TypeVar


class _WireEnum(str, Enum):
    """``str`` mix-in enum whose ``str()`` is the wire value on every supported Python version."""

    def __str__(self) -> str:
        return str(self.value)


class BucketVisibility(_WireEnum):
    """Whether a bucket's files are served anonymously under ``/public/...`` or only via the API."""

    PRIVATE = "Private"
    PUBLIC = "Public"


class FileCategory(_WireEnum):
    """Coarse media-type grouping computed by the server from the content type."""

    IMAGE = "Image"
    VIDEO = "Video"
    AUDIO = "Audio"
    DOCUMENT = "Document"
    OTHER = "Other"


class ThumbnailStatus(_WireEnum):
    """Lifecycle of a file's generated thumbnail."""

    NOT_APPLICABLE = "NotApplicable"
    PENDING = "Pending"
    READY = "Ready"
    FAILED = "Failed"


class FileSortBy(_WireEnum):
    """Columns a file listing can be sorted by (``sortBy`` query parameter)."""

    CREATED_AT_UTC = "CreatedAtUTC"
    ORIGINAL_FILE_NAME = "OriginalFileName"
    SIZE_BYTES = "SizeBytes"
    CONTENT_TYPE = "ContentType"
    BUCKET = "Bucket"


class PresignFailureReason(_WireEnum):
    """Why one target of a batch presign request failed."""

    NOT_FOUND = "NotFound"
    FORBIDDEN = "Forbidden"


class ErrorCode(IntEnum):
    """CDN error codes carried in the error envelope (``CdnApiError.error_code``)."""

    VALIDATION_FAILED = 40001
    UNAUTHORIZED = 40101
    FORBIDDEN = 40301
    NOT_FOUND = 40401
    CONFLICT = 40901
    TOO_MANY_REQUESTS = 42901
    INTERNAL_SERVER_ERROR = 50001


_E = TypeVar("_E", bound=_WireEnum)


def parse_enum(enum_type: type[_E], value: object) -> _E | str:
    """Map a wire value onto ``enum_type``; unknown or non-string values are returned as a string.

    Matching is case-insensitive so a server-side casing change (``CreatedAtUTC`` vs
    ``CreatedAtUtc``) does not break older SDK versions.
    """
    if value is None:
        return ""
    text = str(value)
    folded = text.casefold()
    for member in enum_type:
        if member.value.casefold() == folded:
            return member
    return text


def enum_to_wire(value: object) -> str:
    """Render an enum member (or raw string) as its wire string."""
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)
