"""Tests for the synchronous client. Every request goes through httpx.MockTransport."""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

import httpx
import pytest

from ninjavault_cdn import (
    BucketVisibility,
    CdnApiError,
    CdnConnectionError,
    CdnTimeoutError,
    ErrorCode,
    FileCategory,
    FileListQuery,
    FileSortBy,
    NinjaVaultCdnClient,
    PresignFailureReason,
    PresignTarget,
    ThumbnailStatus,
    __version__,
)

from .conftest import API_KEY, BASE_URL, FILE_OBJECT, Recorder, fail, ok, parse_multipart


def make_client(recorder: Recorder, **kwargs: object) -> NinjaVaultCdnClient:
    http = httpx.Client(transport=httpx.MockTransport(recorder))
    options: dict[str, object] = {"base_url": BASE_URL, "api_key": API_KEY, "http_client": http}
    options.update(kwargs)
    return NinjaVaultCdnClient(**options)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------------------
# Headers, identity, buckets
# --------------------------------------------------------------------------------------------------


def test_get_access_context_sends_api_key_and_parses_envelope(
    client: NinjaVaultCdnClient, recorder: Recorder
) -> None:
    recorder.responses.append(
        ok(
            {
                "name": "billing-service",
                "isUnrestricted": False,
                "isAdmin": False,
                "allowedBuckets": ["documents"],
                "allowedTenantIds": ["42", "0f8fad5b-d9cb-469f-a165-70867728950e"],
                "buckets": [{"name": "documents", "visibility": "Private"}],
                "somethingNew": {"ignored": True},
            }
        )
    )

    access = client.get_access_context()

    request = recorder.last
    assert request.method == "GET"
    assert str(request.url) == f"{BASE_URL}/api/v1/me"
    assert request.headers["X-Api-Key"] == API_KEY
    assert request.headers["User-Agent"] == f"ninjavault-cdn-python/{__version__}"
    assert request.headers["Accept"] == "application/json"
    assert access.name == "billing-service"
    assert access.allowed_tenant_ids == (42, "0f8fad5b-d9cb-469f-a165-70867728950e")
    assert access.buckets[0].visibility is BucketVisibility.PRIVATE


def test_user_agent_suffix_is_appended(recorder: Recorder) -> None:
    recorder.responses.append(ok([]))
    with make_client(recorder, user_agent_suffix="billing/2.1") as cdn:
        cdn.list_buckets()
    assert recorder.last.headers["User-Agent"] == f"ninjavault-cdn-python/{__version__} billing/2.1"


def test_list_buckets(client: NinjaVaultCdnClient, recorder: Recorder) -> None:
    recorder.responses.append(
        ok(
            [
                {
                    "name": "documents",
                    "visibility": "Private",
                    "fileCount": 3,
                    "totalSizeBytes": 10,
                },
                {"name": "assets", "visibility": "Public", "fileCount": 1, "totalSizeBytes": 5},
            ]
        )
    )

    buckets = client.list_buckets()

    assert str(recorder.last.url) == f"{BASE_URL}/api/v1/buckets"
    assert [b.name for b in buckets] == ["documents", "assets"]
    assert buckets[1].visibility is BucketVisibility.PUBLIC
    assert buckets[0].file_count == 3
    assert buckets[0].total_size_bytes == 10


def test_list_buckets_handles_empty_list(client: NinjaVaultCdnClient, recorder: Recorder) -> None:
    recorder.responses.append(ok([]))
    assert client.list_buckets() == []


def test_access_context_tenant_ids_as_strings_from_live_server(
    client: NinjaVaultCdnClient, recorder: Recorder
) -> None:
    # Observed on the real dev server: tenant ids are JSON strings.
    recorder.responses.append(
        ok(
            {
                "name": "restricted",
                "isUnrestricted": False,
                "isAdmin": False,
                "allowedBuckets": ["documents"],
                "allowedTenantIds": ["1"],
                "buckets": [{"name": "other", "visibility": "Public"}],
            }
        )
    )
    access = client.get_access_context()
    assert access.allowed_tenant_ids == (1,)
    assert isinstance(access.allowed_tenant_ids[0], int)


def test_base_url_path_prefix_is_kept(recorder: Recorder) -> None:
    recorder.responses.append(ok([]))
    with make_client(recorder, base_url="https://gateway.test/cdn/") as cdn:
        cdn.list_buckets()
        assert cdn.base_url == "https://gateway.test/cdn"
    assert str(recorder.last.url) == "https://gateway.test/cdn/api/v1/buckets"


# --------------------------------------------------------------------------------------------------
# Upload
# --------------------------------------------------------------------------------------------------

UPLOAD_RESULT = {
    "id": "0f8fad5b-d9cb-469f-a165-70867728950e",
    "bucket": "documents",
    "objectKey": "t/42/o/abc/reports/2026/invoice.pdf",
    "originalFileName": "invoice.pdf",
    "contentType": "application/pdf",
    "sizeBytes": 4,
    "visibility": "Private",
    "url": "https://cdn.test/api/v1/files/documents/t/42/o/abc/reports/2026/invoice.pdf",
    "viewUrl": None,
}


def test_upload_sends_multipart_fields(client: NinjaVaultCdnClient, recorder: Recorder) -> None:
    recorder.responses.append(ok(UPLOAD_RESULT))

    result = client.upload(
        "documents",
        42,
        io.BytesIO(b"%PDF"),
        "invoice.pdf",
        "application/pdf",
        owner_id=1001,
        folder_path="reports/2026",
    )

    request = recorder.last
    assert request.method == "POST"
    assert str(request.url) == f"{BASE_URL}/api/v1/files"
    assert request.headers["X-Api-Key"] == API_KEY
    parts = parse_multipart(request)
    assert list(parts) == ["Bucket", "TenantId", "OwnerId", "FolderPath", "File"]
    assert parts["Bucket"][1] == b"documents"
    assert parts["TenantId"][1] == b"42"
    assert parts["OwnerId"][1] == b"1001"
    assert parts["FolderPath"][1] == b"reports/2026"
    file_headers, file_body = parts["File"]
    assert 'filename="invoice.pdf"' in file_headers["content-disposition"]
    assert file_headers["content-type"] == "application/pdf"
    assert file_body == b"%PDF"
    assert result.object_key == "t/42/o/abc/reports/2026/invoice.pdf"
    assert result.visibility is BucketVisibility.PRIVATE
    assert result.size_bytes == 4
    assert result.view_url is None


def test_upload_omits_optional_fields_and_guesses_content_type(
    client: NinjaVaultCdnClient, recorder: Recorder
) -> None:
    recorder.responses.append(ok(UPLOAD_RESULT))

    client.upload("documents", "0f8fad5b-d9cb-469f-a165-70867728950e", b"%PDF", "a.pdf")

    parts = parse_multipart(recorder.last)
    assert list(parts) == ["Bucket", "TenantId", "File"]
    assert parts["TenantId"][1] == b"0f8fad5b-d9cb-469f-a165-70867728950e"
    assert parts["File"][0]["content-type"] == "application/pdf"


def test_upload_from_path_defaults_file_name_and_closes_file(
    client: NinjaVaultCdnClient, recorder: Recorder, tmp_path: Path
) -> None:
    recorder.responses.append(ok(UPLOAD_RESULT))
    source = tmp_path / "photo.png"
    source.write_bytes(b"\x89PNG")

    client.upload("documents", 42, source, folder_path="   ")

    parts = parse_multipart(recorder.last)
    assert "FolderPath" not in parts
    assert 'filename="photo.png"' in parts["File"][0]["content-disposition"]
    assert parts["File"][0]["content-type"] == "image/png"
    assert parts["File"][1] == b"\x89PNG"
    source.unlink()  # would fail on Windows if the SDK left the file open


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"bucket": " ", "tenant_id": 1, "file": b"x", "file_name": "a.txt"}, ValueError),
        ({"bucket": "b", "tenant_id": " ", "file": b"x", "file_name": "a.txt"}, ValueError),
        ({"bucket": "b", "tenant_id": True, "file": b"x", "file_name": "a.txt"}, TypeError),
        ({"bucket": "b", "tenant_id": 1, "file": b"x", "file_name": None}, ValueError),
        ({"bucket": "b", "tenant_id": 1, "file": 123, "file_name": "a.txt"}, TypeError),
    ],
)
def test_upload_validates_arguments(
    client: NinjaVaultCdnClient, recorder: Recorder, kwargs: dict[str, object], error: type
) -> None:
    with pytest.raises(error):
        client.upload(**kwargs)  # type: ignore[arg-type]
    assert recorder.requests == []


def test_upload_from_path_without_name_is_closed_when_invalid(
    client: NinjaVaultCdnClient, tmp_path: Path
) -> None:
    source = tmp_path / "x.bin"
    source.write_bytes(b"x")
    with pytest.raises(ValueError, match="file_name"):
        client.upload("documents", 1, source, file_name=" ")
    source.unlink()


# --------------------------------------------------------------------------------------------------
# List / metadata / summary
# --------------------------------------------------------------------------------------------------


def test_list_files_builds_server_query_names(
    client: NinjaVaultCdnClient, recorder: Recorder
) -> None:
    recorder.responses.append(
        ok({"items": [FILE_OBJECT], "totalCount": 1, "page": 2, "pageSize": 10})
    )
    query = FileListQuery(
        bucket="documents",
        tenant_id=42,
        owner_id=1001,
        object_key_prefix="reports/2026",
        file_name_contains="in voice",
        content_type="application/pdf",
        category=FileCategory.DOCUMENT,
        visibility=BucketVisibility.PRIVATE,
        created_from_utc=datetime(2026, 9, 1, tzinfo=timezone.utc),
        created_to_utc=datetime(2026, 9, 28, 12, 0, tzinfo=timezone(timedelta(hours=5))),
        include_deleted=False,
        sort_by=FileSortBy.CREATED_AT_UTC,
        sort_descending=True,
        page=2,
        page_size=10,
    )

    page = client.list_files(query)

    assert str(recorder.last.url) == (
        f"{BASE_URL}/api/v1/files?bucket=documents&tenantId=42&ownerId=1001"
        "&objectKeyPrefix=reports%2F2026&fileNameContains=in%20voice"
        "&contentType=application%2Fpdf&category=Document&visibility=Private"
        "&createdFromUTC=2026-09-01T00%3A00%3A00Z&createdToUTC=2026-09-28T07%3A00%3A00Z"
        "&includeDeleted=false&sortBy=CreatedAtUTC&sortDescending=true&page=2&pageSize=10"
    )
    assert page.total_count == 1
    assert page.page == 2
    assert page.page_size == 10
    item = page.items[0]
    assert item.tenant_id == 42
    assert item.owner_id == 1001
    assert item.category is FileCategory.DOCUMENT
    assert item.thumbnail_status is ThumbnailStatus.NOT_APPLICABLE
    assert item.created_at_utc == datetime(2026, 9, 28, 10, 11, 12, 123456, tzinfo=timezone.utc)


def test_list_files_without_query_and_with_keyword_filters(
    client: NinjaVaultCdnClient, recorder: Recorder
) -> None:
    recorder.responses.append(ok({"items": [], "totalCount": 0, "page": 1, "pageSize": 50}))

    client.list_files()
    assert str(recorder.last.url) == f"{BASE_URL}/api/v1/files"

    client.list_files(FileListQuery(bucket="documents", page=3), page=4, sort_by="SizeBytes")
    assert (
        str(recorder.last.url)
        == f"{BASE_URL}/api/v1/files?bucket=documents&sortBy=SizeBytes&page=4"
    )

    client.list_files(bucket="  ", include_deleted=True)
    assert str(recorder.last.url) == f"{BASE_URL}/api/v1/files?includeDeleted=true"


def test_list_files_rejects_unknown_filter(client: NinjaVaultCdnClient) -> None:
    with pytest.raises(TypeError):
        client.list_files(not_a_filter=1)


def test_naive_datetime_filters_are_treated_as_utc() -> None:
    params = FileListQuery(created_from_utc=datetime(2026, 1, 2, 3, 4, 5)).to_params()
    assert params == [("createdFromUTC", "2026-01-02T03:04:05Z")]


def test_get_metadata_encodes_bucket_and_object_key_segments(
    client: NinjaVaultCdnClient, recorder: Recorder
) -> None:
    recorder.responses.append(ok(FILE_OBJECT))

    info = client.get_metadata("my bucket", "/reports//2026 Q1/a+b&c?.pdf/ü")

    assert recorder.last.url.raw_path == (
        b"/api/v1/file-metadata/my%20bucket/reports/2026%20Q1/a%2Bb%26c%3F.pdf/%C3%BC"
    )
    assert info.checksum == "ab12"


@pytest.mark.parametrize(("bucket", "key"), [("", "a"), ("b", ""), ("b", "///"), (" ", "a")])
def test_object_routes_reject_blank_bucket_or_key(
    client: NinjaVaultCdnClient, bucket: str, key: str
) -> None:
    with pytest.raises(ValueError, match=r"bucket|object_key"):
        client.get_metadata(bucket, key)


def test_get_summary(client: NinjaVaultCdnClient, recorder: Recorder) -> None:
    recorder.responses.append(
        ok(
            {
                "totalFileCount": 3,
                "totalSizeBytes": 30,
                "categories": [{"category": "Image", "fileCount": 3, "totalSizeBytes": 30}],
            }
        )
    )

    summary = client.get_summary()
    assert str(recorder.last.url) == f"{BASE_URL}/api/v1/files/summary"
    assert summary.total_file_count == 3
    assert summary.categories[0].category is FileCategory.IMAGE

    client.get_summary("my docs")
    assert str(recorder.last.url) == f"{BASE_URL}/api/v1/files/summary?bucket=my%20docs"


def test_unknown_enum_values_are_kept_as_strings(
    client: NinjaVaultCdnClient, recorder: Recorder
) -> None:
    recorder.responses.append(
        ok(
            {
                **FILE_OBJECT,
                "visibility": "Internal",
                "category": "Model3D",
                "thumbnailStatus": "ready",
            }
        )
    )

    info = client.get_metadata("documents", "k")

    assert info.visibility == "Internal"
    assert info.category == "Model3D"
    assert info.thumbnail_status is ThumbnailStatus.READY  # case-insensitive match


# --------------------------------------------------------------------------------------------------
# Presign and public URLs
# --------------------------------------------------------------------------------------------------


def test_create_presigned_url_sends_json_body(
    client: NinjaVaultCdnClient, recorder: Recorder
) -> None:
    recorder.responses.append(
        ok(
            {
                "url": "https://cdn.test/files/presigned/d/k?expires=1&sig=x",
                "expiresAtUtc": "2026-09-28T10:00:00+00:00",
            }
        )
    )

    link = client.create_presigned_url("documents", "a/b.pdf", expiry_seconds=600)

    assert recorder.last.method == "POST"
    assert str(recorder.last.url) == f"{BASE_URL}/api/v1/files/presign"
    assert recorder.last.headers["Content-Type"] == "application/json"
    assert recorder.json_body() == {
        "bucket": "documents",
        "objectKey": "a/b.pdf",
        "expirySeconds": 600,
    }
    assert link.expires_at_utc == datetime(2026, 9, 28, 10, tzinfo=timezone.utc)

    client.create_presigned_url("documents", "a/b.pdf")
    assert recorder.json_body() == {"bucket": "documents", "objectKey": "a/b.pdf"}


def test_create_presigned_urls_batch(client: NinjaVaultCdnClient, recorder: Recorder) -> None:
    recorder.responses.append(
        ok(
            {
                "succeeded": [
                    {
                        "bucket": "documents",
                        "objectKey": "a",
                        "url": "https://cdn.test/files/presigned/documents/a?sig=x",
                        "expiresAtUtc": "2026-09-28T10:00:00Z",
                    }
                ],
                "failed": [
                    {"bucket": "documents", "objectKey": "b", "reason": "NotFound"},
                    {"bucket": "documents", "objectKey": "c", "reason": "Expired"},
                ],
            }
        )
    )

    result = client.create_presigned_urls(
        [("documents", "a"), PresignTarget("documents", "b"), ("documents", "c")],
        expiry_seconds=300,
    )

    assert str(recorder.last.url) == f"{BASE_URL}/api/v1/files/presign/batch"
    assert recorder.json_body() == {
        "targets": [
            {"bucket": "documents", "objectKey": "a"},
            {"bucket": "documents", "objectKey": "b"},
            {"bucket": "documents", "objectKey": "c"},
        ],
        "expirySeconds": 300,
    }
    assert result.succeeded[0].object_key == "a"
    assert result.failed[0].reason is PresignFailureReason.NOT_FOUND
    assert result.failed[1].reason == "Expired"


def test_create_presigned_urls_validates_targets(client: NinjaVaultCdnClient) -> None:
    with pytest.raises(ValueError, match="object_key"):
        client.create_presigned_urls([("documents", "")])


@pytest.mark.parametrize(
    ("public_base_url", "expected"),
    [
        (None, "https://cdn.test/public/assets/logos/my%20logo.png"),
        ("https://static.test", "https://static.test/public/assets/logos/my%20logo.png"),
        ("https://static.test/", "https://static.test/public/assets/logos/my%20logo.png"),
        # The server's own PublicBaseUrl setting ends in /public: never produce /public/public/.
        ("https://static.test/public", "https://static.test/public/assets/logos/my%20logo.png"),
        ("https://static.test/public/", "https://static.test/public/assets/logos/my%20logo.png"),
    ],
)
def test_build_public_url(recorder: Recorder, public_base_url: str | None, expected: str) -> None:
    with make_client(recorder, public_base_url=public_base_url) as cdn:
        url = cdn.build_public_url("assets", "logos/my logo.png")
    assert url == expected
    assert "/public/public/" not in url
    assert recorder.requests == []


# --------------------------------------------------------------------------------------------------
# Delete
# --------------------------------------------------------------------------------------------------


def test_delete_accepts_no_content(client: NinjaVaultCdnClient, recorder: Recorder) -> None:
    recorder.responses.append(httpx.Response(204))

    client.delete("documents", "a/b c.pdf")

    assert recorder.last.method == "DELETE"
    assert recorder.last.url.raw_path == b"/api/v1/files/documents/a/b%20c.pdf"
    assert recorder.last.headers["X-Api-Key"] == API_KEY


def test_delete_accepts_success_envelope_and_non_json(
    client: NinjaVaultCdnClient, recorder: Recorder
) -> None:
    recorder.responses.extend([ok(None), httpx.Response(200, text="deleted")])
    client.delete("documents", "a")
    client.delete("documents", "a")


def test_delete_raises_on_failure_envelope(client: NinjaVaultCdnClient, recorder: Recorder) -> None:
    recorder.responses.append(
        httpx.Response(
            200,
            json={
                "success": False,
                "data": None,
                "error": {"errorCode": 40901, "message": "Conflict", "traceId": "t-1"},
            },
        )
    )

    with pytest.raises(CdnApiError) as caught:
        client.delete("documents", "a")

    assert caught.value.status_code == 200
    assert caught.value.error_code == ErrorCode.CONFLICT
    assert caught.value.correlation_id == "t-1"


def test_delete_raises_on_http_error(client: NinjaVaultCdnClient, recorder: Recorder) -> None:
    recorder.responses.append(fail(404, 40401, "File not found."))
    with pytest.raises(CdnApiError) as caught:
        client.delete("documents", "missing")
    assert caught.value.error_code == ErrorCode.NOT_FOUND


# --------------------------------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------------------------------


def test_error_envelope_maps_to_cdn_api_error(
    client: NinjaVaultCdnClient, recorder: Recorder
) -> None:
    recorder.responses.append(
        fail(
            400,
            40001,
            "Validation failed.",
            description="One or more fields are invalid.",
            trace_id="0HN7-abc",
            details={"FolderPath": ["Segment 'a b' is invalid."]},
        )
    )

    with pytest.raises(CdnApiError) as caught:
        client.upload("documents", 42, b"x", "a.pdf", folder_path="a b")

    error = caught.value
    assert error.status_code == 400
    assert error.error_code == ErrorCode.VALIDATION_FAILED
    assert error.message == "Validation failed."
    assert error.description == "One or more fields are invalid."
    assert error.correlation_id == "0HN7-abc"
    assert error.details == {"FolderPath": ["Segment 'a b' is invalid."]}
    assert error.retry_after is None
    assert str(error) == "HTTP 400 error 40001: Validation failed. (traceId 0HN7-abc)"
    assert "40001" in repr(error)


def test_rate_limit_exposes_retry_after_delta(
    client: NinjaVaultCdnClient, recorder: Recorder
) -> None:
    recorder.responses.append(fail(429, 42901, "Too many requests.", headers={"Retry-After": "7"}))
    with pytest.raises(CdnApiError) as caught:
        client.list_buckets()
    assert caught.value.error_code == ErrorCode.TOO_MANY_REQUESTS
    assert caught.value.retry_after == 7.0


def test_rate_limit_exposes_retry_after_http_date(
    client: NinjaVaultCdnClient, recorder: Recorder
) -> None:
    when = datetime.now(timezone.utc) + timedelta(seconds=120)
    recorder.responses.append(
        fail(
            429,
            42901,
            "Too many requests.",
            headers={"Retry-After": format_datetime(when, usegmt=True)},
        )
    )
    with pytest.raises(CdnApiError) as caught:
        client.list_buckets()
    assert caught.value.retry_after is not None
    assert 100 < caught.value.retry_after <= 120


@pytest.mark.parametrize(
    ("header", "expected"),
    [("garbage", None), ("Wed, 21 Oct 2015 07:28:00 GMT", 0.0), ("-5", 0.0), ("  ", None)],
)
def test_retry_after_edge_cases(
    client: NinjaVaultCdnClient, recorder: Recorder, header: str, expected: float | None
) -> None:
    recorder.responses.append(
        fail(429, 42901, "Too many requests.", headers={"Retry-After": header})
    )
    with pytest.raises(CdnApiError) as caught:
        client.list_buckets()
    assert caught.value.retry_after == expected


def test_non_json_error_body_is_mapped(client: NinjaVaultCdnClient, recorder: Recorder) -> None:
    recorder.responses.append(httpx.Response(502, text="<html>Bad gateway</html>"))

    with pytest.raises(CdnApiError) as caught:
        client.list_buckets()

    error = caught.value
    assert error.status_code == 502
    assert error.error_code is None
    assert error.message == "CDN request failed with HTTP 502."
    assert error.description == "<html>Bad gateway</html>"
    assert error.correlation_id is None
    assert str(error) == "HTTP 502: CDN request failed with HTTP 502."


def test_empty_and_json_non_envelope_error_bodies(
    client: NinjaVaultCdnClient, recorder: Recorder
) -> None:
    recorder.responses.extend([httpx.Response(401), httpx.Response(500, json=["x"])])
    with pytest.raises(CdnApiError) as first:
        client.list_buckets()
    assert first.value.status_code == 401
    assert first.value.description is None
    with pytest.raises(CdnApiError) as second:
        client.list_buckets()
    assert second.value.description == '["x"]'


def test_long_raw_error_body_is_truncated(client: NinjaVaultCdnClient, recorder: Recorder) -> None:
    recorder.responses.append(httpx.Response(500, text="x" * 10_000))
    with pytest.raises(CdnApiError) as caught:
        client.list_buckets()
    assert caught.value.description is not None
    assert len(caught.value.description) == 4096 + 3


def test_error_envelope_with_sparse_fields(client: NinjaVaultCdnClient, recorder: Recorder) -> None:
    recorder.responses.append(
        httpx.Response(
            403,
            json={
                "success": False,
                "error": {"errorCode": "40301", "details": {"Bucket": "not allowed", "X": None}},
            },
        )
    )
    with pytest.raises(CdnApiError) as caught:
        client.list_buckets()
    error = caught.value
    assert error.error_code == ErrorCode.FORBIDDEN
    assert error.message == "CDN request failed with HTTP 403."
    assert error.details == {"Bucket": ["not allowed"]}
    assert error.correlation_id is None


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (
            httpx.Response(200, json={"success": False, "data": None, "error": None}),
            "CDN request failed.",
        ),
        (httpx.Response(200), "CDN response body was empty."),
        (httpx.Response(200, text="not json"), "CDN response was not valid JSON."),
        (httpx.Response(200, json=[1, 2]), "CDN response was not a JSON envelope."),
        (
            httpx.Response(200, json={"success": True, "data": None}),
            "CDN response data was not an object.",
        ),
    ],
)
def test_bad_success_responses(
    client: NinjaVaultCdnClient, recorder: Recorder, response: httpx.Response, message: str
) -> None:
    recorder.responses.append(response)
    with pytest.raises(CdnApiError, match=message):
        client.get_access_context()


def test_list_route_requires_list_data(client: NinjaVaultCdnClient, recorder: Recorder) -> None:
    recorder.responses.append(ok({"not": "a list"}))
    with pytest.raises(CdnApiError, match="not a list"):
        client.list_buckets()


def test_transport_errors_are_wrapped(client: NinjaVaultCdnClient, recorder: Recorder) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    recorder.responses.extend([refuse, slow])

    with pytest.raises(CdnConnectionError) as refused:
        client.list_buckets()
    assert not isinstance(refused.value, CdnTimeoutError)
    assert isinstance(refused.value.__cause__, httpx.ConnectError)

    with pytest.raises(CdnTimeoutError):
        client.list_buckets()


# --------------------------------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------------------------------


def _file_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        content=b"0123456789" * 1000,
        headers={
            "Content-Type": "application/pdf; charset=binary",
            "Content-Disposition": (
                "attachment; filename=report.pdf; filename*=UTF-8''r%C3%A9port.pdf"
            ),
            "ETag": '"ab12"',
        },
    )


def test_download_streams_content_and_metadata(
    client: NinjaVaultCdnClient, recorder: Recorder
) -> None:
    recorder.responses.append(_file_response)

    with client.download("documents", "reports/a b.pdf") as download:
        assert download.content_type == "application/pdf"
        assert download.file_name == "réport.pdf"
        assert download.size_bytes == 10_000
        assert download.etag == '"ab12"'
        assert download.response.status_code == 200
        assert "réport.pdf" in repr(download)
        chunks = list(download.iter_bytes(4096))

    assert recorder.last.method == "GET"
    assert recorder.last.url.raw_path == b"/api/v1/files/documents/reports/a%20b.pdf"
    assert recorder.last.headers["X-Api-Key"] == API_KEY
    assert recorder.last.headers["Accept"] == "*/*"
    assert b"".join(chunks) == b"0123456789" * 1000
    assert len(chunks) == 3


def test_download_read_and_plain_filename(client: NinjaVaultCdnClient, recorder: Recorder) -> None:
    recorder.responses.append(
        httpx.Response(
            200,
            content=b"hello",
            headers={"Content-Disposition": 'attachment; filename="a b.txt"'},
        )
    )
    download = client.download("documents", "a")
    try:
        assert download.read() == b"hello"
        assert download.file_name == "a b.txt"
        assert download.content_type is None
    finally:
        download.close()
        download.close()


def test_download_error_is_mapped(client: NinjaVaultCdnClient, recorder: Recorder) -> None:
    recorder.responses.append(fail(404, 40401, "File not found.", trace_id="t-9"))
    with pytest.raises(CdnApiError) as caught:
        client.download("documents", "missing")
    assert caught.value.error_code == ErrorCode.NOT_FOUND
    assert caught.value.correlation_id == "t-9"


def test_download_to_path_and_file_object(
    client: NinjaVaultCdnClient, recorder: Recorder, tmp_path: Path
) -> None:
    recorder.responses.append(_file_response)

    target = tmp_path / "out.pdf"
    written = client.download_to("documents", "a", target, chunk_size=1024)
    assert written == 10_000
    assert target.read_bytes() == b"0123456789" * 1000

    sink = io.BytesIO()
    assert client.download_to("documents", "a", sink) == 10_000
    assert sink.getvalue() == b"0123456789" * 1000

    assert client.download_to("documents", "a", str(tmp_path / "out2.pdf")) == 10_000


class _BrokenStream(httpx.SyncByteStream):
    def __iter__(self) -> Iterator[bytes]:
        yield b"partial"
        raise httpx.ReadError("connection reset")


def test_download_to_removes_partial_file_on_failure(
    client: NinjaVaultCdnClient, recorder: Recorder, tmp_path: Path
) -> None:
    recorder.responses.append(httpx.Response(200, stream=_BrokenStream()))
    target = tmp_path / "partial.bin"

    with pytest.raises(CdnConnectionError, match="download failed"):
        client.download_to("documents", "a", target)

    assert not target.exists()


def test_download_read_wraps_transport_errors(
    client: NinjaVaultCdnClient, recorder: Recorder
) -> None:
    recorder.responses.append(httpx.Response(200, stream=_BrokenStream()))
    with client.download("documents", "a") as download, pytest.raises(CdnConnectionError):
        download.read()


def _raise_timeout() -> None:
    raise httpx.ReadTimeout("slow")


def test_download_timeout_while_streaming(client: NinjaVaultCdnClient, recorder: Recorder) -> None:
    class _SlowStream(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            _raise_timeout()
            yield b""  # pragma: no cover

    recorder.responses.append(httpx.Response(200, stream=_SlowStream()))
    with client.download("documents", "a") as download, pytest.raises(CdnTimeoutError):
        list(download.iter_bytes())


# --------------------------------------------------------------------------------------------------
# Configuration and lifecycle
# --------------------------------------------------------------------------------------------------


def test_configuration_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NINJAVAULT_CDN_BASE_URL", "https://env.test/")
    monkeypatch.setenv("NINJAVAULT_CDN_API_KEY", "cdn_xxxxx")
    monkeypatch.setenv("NINJAVAULT_CDN_PUBLIC_BASE_URL", "https://static.env.test")

    with NinjaVaultCdnClient() as cdn:
        assert cdn.base_url == "https://env.test"
        assert cdn.build_public_url("b", "k") == "https://static.env.test/public/b/k"
        assert "cdn_xxxxx" not in repr(cdn)


def test_explicit_arguments_override_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NINJAVAULT_CDN_BASE_URL", "https://env.test")
    monkeypatch.setenv("NINJAVAULT_CDN_API_KEY", "cdn_env")
    with NinjaVaultCdnClient(base_url="https://arg.test", api_key="cdn_arg") as cdn:
        assert cdn.base_url == "https://arg.test"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"api_key": "cdn_xxxxx"}, "base_url.*NINJAVAULT_CDN_BASE_URL"),
        ({"base_url": "https://cdn.test"}, "api_key.*NINJAVAULT_CDN_API_KEY"),
        ({"base_url": "https://cdn.test", "api_key": "   "}, "api_key"),
        ({"base_url": "cdn.test", "api_key": "cdn_xxxxx"}, "absolute http"),
        ({"base_url": "ftp://cdn.test", "api_key": "cdn_xxxxx"}, "absolute http"),
        (
            {"base_url": "https://cdn.test", "api_key": "k", "public_base_url": "/relative"},
            "public_base_url",
        ),
    ],
)
def test_missing_or_invalid_configuration(kwargs: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        NinjaVaultCdnClient(**kwargs)  # type: ignore[arg-type]


def test_user_supplied_http_client_is_not_closed(recorder: Recorder) -> None:
    http = httpx.Client(transport=httpx.MockTransport(recorder))
    with NinjaVaultCdnClient(base_url=BASE_URL, api_key=API_KEY, http_client=http):
        pass
    assert not http.is_closed
    http.close()


def test_owned_http_client_is_closed() -> None:
    cdn = NinjaVaultCdnClient(base_url=BASE_URL, api_key=API_KEY, timeout=5)
    inner = cdn._client
    assert inner.timeout.read == 5
    cdn.close()
    assert inner.is_closed


def test_event_hooks_run_for_every_request(
    monkeypatch: pytest.MonkeyPatch, recorder: Recorder
) -> None:
    recorder.responses.append(ok([]))
    real_client = httpx.Client

    def client_with_mock_transport(**kwargs: object) -> httpx.Client:
        return real_client(transport=httpx.MockTransport(recorder), **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "Client", client_with_mock_transport)
    seen: list[str] = []

    def add_correlation_id(request: httpx.Request) -> None:
        request.headers["X-Correlation-Id"] = "corr-1"

    def log_response(response: httpx.Response) -> None:
        seen.append(f"{response.request.method} {response.status_code}")

    with NinjaVaultCdnClient(
        base_url=BASE_URL,
        api_key=API_KEY,
        event_hooks={"request": [add_correlation_id], "response": [log_response]},
    ) as cdn:
        cdn.list_buckets()

    assert recorder.last.headers["X-Correlation-Id"] == "corr-1"
    assert seen == ["GET 200"]


def test_event_hooks_and_http_client_are_mutually_exclusive(recorder: Recorder) -> None:
    http = httpx.Client(transport=httpx.MockTransport(recorder))
    with pytest.raises(ValueError, match="event_hooks"):
        NinjaVaultCdnClient(
            base_url=BASE_URL, api_key=API_KEY, http_client=http, event_hooks={"request": []}
        )
    http.close()


def test_debug_logging_never_contains_api_key(
    client: NinjaVaultCdnClient, recorder: Recorder, caplog: pytest.LogCaptureFixture
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    recorder.responses.extend([ok([]), refuse])
    caplog.set_level(logging.DEBUG, logger="ninjavault_cdn")

    client.list_buckets()
    with pytest.raises(CdnConnectionError):
        client.list_buckets()

    messages = [record.getMessage() for record in caplog.records if record.name == "ninjavault_cdn"]
    assert messages[0].startswith(f"GET {BASE_URL}/api/v1/buckets -> 200 (")
    assert "failed after" in messages[1]
    assert "ConnectError" in messages[1]
    assert all(API_KEY not in message for message in messages)
    assert logging.getLogger("ninjavault_cdn").handlers == []
