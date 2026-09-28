"""Model parsing, enums and datetime handling."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

import ninjavault_cdn
from ninjavault_cdn import (
    AccessContext,
    BucketVisibility,
    ErrorCode,
    FileCategory,
    FileObject,
    FileSortBy,
    PagedResult,
    PresignTarget,
    ThumbnailStatus,
)
from ninjavault_cdn._enums import enum_to_wire, parse_enum
from ninjavault_cdn._models import MIN_DATETIME, format_datetime, parse_datetime

from .conftest import FILE_OBJECT


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "2026-09-28T10:11:12.1234567Z",
            datetime(2026, 9, 28, 10, 11, 12, 123456, tzinfo=timezone.utc),
        ),
        ("2026-09-28T10:11:12Z", datetime(2026, 9, 28, 10, 11, 12, tzinfo=timezone.utc)),
        ("2026-09-28T10:11:12.5", datetime(2026, 9, 28, 10, 11, 12, 500000, tzinfo=timezone.utc)),
        ("2026-09-28 10:11", datetime(2026, 9, 28, 10, 11, tzinfo=timezone.utc)),
        (
            "2026-09-28T15:11:12+05:00",
            datetime(2026, 9, 28, 15, 11, 12, tzinfo=timezone(timedelta(hours=5))),
        ),
        (
            "2026-09-28T15:11:12-0130",
            datetime(2026, 9, 28, 15, 11, 12, tzinfo=timezone(-timedelta(hours=1, minutes=30))),
        ),
    ],
)
def test_parse_datetime_formats(text: str, expected: datetime) -> None:
    parsed = parse_datetime(text)
    assert parsed == expected
    assert parsed is not None
    assert parsed.tzinfo is not None


@pytest.mark.parametrize("value", [None, "", "yesterday", "2026-13-45T00:00:00Z", 12])
def test_parse_datetime_invalid(value: object) -> None:
    assert parse_datetime(value) is None


def test_parse_datetime_passthrough() -> None:
    aware = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert parse_datetime(aware) is aware
    assert parse_datetime(datetime(2026, 1, 1)) == aware


def test_format_datetime() -> None:
    assert format_datetime(datetime(2026, 1, 1, 5, tzinfo=timezone(timedelta(hours=5)))) == (
        "2026-01-01T00:00:00Z"
    )


def test_file_object_is_tolerant() -> None:
    info = FileObject.from_dict(
        {
            "ID": "x",
            "tenantId": 42,
            "ownerId": "17",
            "sizeBytes": 12.0,
            "createdAtUtc": "not a date",
            "brandNewField": [1, 2, 3],
        }
    )
    assert info.id == "x"
    assert info.tenant_id == 42
    assert info.owner_id == 17
    assert info.size_bytes == 12
    assert info.created_at_utc == MIN_DATETIME
    assert info.visibility == ""
    assert info.checksum is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", 1),
        (7, 7),
        (" 42 ", 42),
        ("0f8fad5b-d9cb-469f-a165-70867728950e", "0f8fad5b-d9cb-469f-a165-70867728950e"),
        (None, ""),
    ],
)
def test_tenant_ids_accept_numbers_numeric_strings_and_guids(raw: object, expected: object) -> None:
    assert FileObject.from_dict({"tenantId": raw}).tenant_id == expected
    if raw is not None:
        assert AccessContext.from_dict({"allowedTenantIds": [raw, None]}).allowed_tenant_ids == (
            expected,
        )


def test_int_and_bool_coercion() -> None:
    info = FileObject.from_dict({**FILE_OBJECT, "ownerId": "abc", "sizeBytes": True})
    assert info.owner_id is None
    assert info.size_bytes == 0
    access = AccessContext.from_dict(
        {"isUnrestricted": "true", "isAdmin": "False", "allowedBuckets": "nope", "buckets": [1, {}]}
    )
    assert access.is_unrestricted is True
    assert access.is_admin is False
    assert access.allowed_buckets == ()
    assert len(access.buckets) == 1


def test_models_are_frozen() -> None:
    info = FileObject.from_dict(FILE_OBJECT)
    with pytest.raises(dataclasses.FrozenInstanceError):
        info.bucket = "other"  # type: ignore[misc]


def test_paged_result_generic() -> None:
    page = PagedResult.from_dict(
        {"items": [FILE_OBJECT, "junk"], "totalCount": 9}, FileObject.from_dict
    )
    assert len(page.items) == 1
    assert page.total_count == 9
    assert page.page == 0


def test_enums() -> None:
    assert str(BucketVisibility.PUBLIC) == "Public"
    assert FileSortBy.CREATED_AT_UTC.value == "CreatedAtUTC"
    assert parse_enum(FileCategory, "image") is FileCategory.IMAGE
    assert parse_enum(ThumbnailStatus, None) == ""
    assert parse_enum(ThumbnailStatus, 3) == "3"
    assert enum_to_wire(FileSortBy.SIZE_BYTES) == "SizeBytes"
    assert enum_to_wire(12) == "12"
    assert int(ErrorCode.TOO_MANY_REQUESTS) == 42901
    assert {code.value for code in ErrorCode} == {40001, 40101, 40301, 40401, 40901, 42901, 50001}


def test_presign_target_to_dict() -> None:
    assert PresignTarget("b", "k").to_dict() == {"bucket": "b", "objectKey": "k"}


def test_public_api_is_exported() -> None:
    for name in ninjavault_cdn.__all__:
        assert hasattr(ninjavault_cdn, name), name
    assert ninjavault_cdn.__version__ == "100.42.0.1"
