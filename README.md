<p align="center">
  <img src="https://raw.githubusercontent.com/alisaivi786/NinjaVault/main/assets/icon-512.png" width="112" alt="NinjaVault" />
</p>

<h1 align="center">ninjavault-cdn</h1>

<p align="center">
  Typed Python client (sync and async) for the <b>NinjaVault CDN Server</b>: upload, search, download and share files.
</p>

<p align="center">
  <a href="https://pypi.org/project/ninjavault-cdn/"><img src="https://img.shields.io/pypi/v/ninjavault-cdn.svg?label=ninjavault-cdn" alt="PyPI version" /></a>
  <a href="https://pypi.org/project/ninjavault-cdn/"><img src="https://img.shields.io/pypi/dm/ninjavault-cdn.svg" alt="PyPI downloads" /></a>
  <a href="https://pypi.org/project/ninjavault-cdn/"><img src="https://img.shields.io/pypi/pyversions/ninjavault-cdn.svg" alt="Python versions" /></a>
  <a href="https://github.com/alisaivi786/NinjaVault-Python/actions/workflows/ci.yml"><img src="https://github.com/alisaivi786/NinjaVault-Python/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <a href="https://github.com/alisaivi786/NinjaVault-Python/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT" /></a>
</p>

---

## Why ninjavault-cdn

- **One client, every operation.** Upload, list/search, metadata, usage summary, download, soft delete, public URLs,
  and single or batch presigned URLs.
- **Sync and async, same surface.** `NinjaVaultCdnClient` (on `httpx.Client`) and `AsyncNinjaVaultCdnClient`
  (on `httpx.AsyncClient`) have identical methods.
- **No HTTP plumbing.** Handles the `X-Api-Key` header, multipart fields, object-key encoding and the JSON
  success/error envelope for you.
- **Typed errors.** Every failure is a `CdnApiError` with the HTTP status, CDN error code, trace id, validation
  details and `Retry-After`. Network failures are `CdnConnectionError` / `CdnTimeoutError`.
- **Typed models.** Frozen dataclasses, `py.typed`, `mypy --strict` clean. A newer server never breaks an older SDK:
  unknown fields are ignored and unknown enum values are kept as strings.
- **Streams large files.** Downloads are streamed; `download_to` writes to disk chunk by chunk.
- **Standalone, no opinions.** Only depends on `httpx`. Logging, retries and correlation stay under your app's control.

---

## Quick start

**1. Install**

```bash
pip install ninjavault-cdn
```

**2. Configure** (keep the key out of source control)

```bash
export NINJAVAULT_CDN_BASE_URL="https://cdn.example.com"
export NINJAVAULT_CDN_API_KEY="cdn_xxxxx"
```

**3. Use it (sync)**

```python
from pathlib import Path
from ninjavault_cdn import NinjaVaultCdnClient

with NinjaVaultCdnClient() as cdn:                  # reads NINJAVAULT_CDN_* env vars
    access = cdn.get_access_context()               # verifies the key
    uploaded = cdn.upload("documents", 42, Path("invoice.pdf"), content_type="application/pdf")
    print(uploaded.bucket, uploaded.object_key)     # store these in your database
    link = cdn.create_presigned_url("documents", uploaded.object_key, expiry_seconds=600)
    print(link.url)                                 # safe to hand to a browser
```

**...or async**

```python
import asyncio
from ninjavault_cdn import AsyncNinjaVaultCdnClient

async def main() -> None:
    async with AsyncNinjaVaultCdnClient() as cdn:
        uploaded = await cdn.upload("documents", 42, b"%PDF-1.7 ...", "invoice.pdf", "application/pdf")
        page = await cdn.list_files(bucket="documents", tenant_id=42, page_size=20)
        print(uploaded.object_key, page.total_count)

asyncio.run(main())
```

That's it. Everything below is optional.

---

## Configuration reference

Pass keyword arguments, or leave them out to use environment variables:

```python
cdn = NinjaVaultCdnClient(
    base_url="https://cdn.example.com",
    api_key=settings.cdn_api_key,
    public_base_url="https://static.example.com",   # optional
    timeout=30.0,                                   # optional
)
```

| Argument | Environment variable | Required | Default | Description |
|---|---|:---:|---|---|
| `base_url` | `NINJAVAULT_CDN_BASE_URL` | yes | none | Base URL of the authenticated `/api/v1/...` routes. A path prefix works (`https://gateway.example.com/cdn`). |
| `api_key` | `NINJAVAULT_CDN_API_KEY` | yes | none | The `X-Api-Key` issued for your integration. **Backend-only secret.** |
| `public_base_url` | `NINJAVAULT_CDN_PUBLIC_BASE_URL` | | `base_url` | Host for anonymous public files. The client appends `/public/`. |
| `timeout` | | | `30` seconds | Seconds, or an `httpx.Timeout`. Applies to the client the SDK creates. |
| `http_client` | | | new client | Your own `httpx.Client` / `httpx.AsyncClient` (proxies, mTLS, custom transport). **Never closed by the SDK.** Configure `timeout` and event hooks on it. |
| `event_hooks` | | | none | `httpx` event hooks for your own logging or correlation headers (see [Logging](#logging-and-correlation)). Async hooks for the async client. |
| `user_agent_suffix` | | | none | Appended to `User-Agent: ninjavault-cdn-python/<version>`. |

Explicit arguments win over environment variables. A missing `base_url` or `api_key` raises `ValueError` naming the
argument and its environment variable. Create one client per process and reuse it: it holds a connection pool.

### Verify the key at startup (recommended)

```python
access = cdn.get_access_context()
# access.allowed_buckets / access.allowed_tenant_ids empty = unrestricted, not "no access"
```

Tenant ids are `int` when numeric (the server sends them as strings, e.g. `["1"]`) and stay `str` for GUID tenants.

---

## Usage

All examples use the sync client. With `AsyncNinjaVaultCdnClient`, `await` the same calls.

### Upload

```python
uploaded = cdn.upload(
    "documents",                 # bucket
    42,                          # tenant id: int or GUID string
    Path("report.docx"),         # bytes, a binary file object, or a pathlib.Path
    file_name="report.docx",     # defaults to the Path's name
    content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    owner_id=1001,               # optional
    folder_path="reports/2026",  # optional: up to 8 segments of [A-Za-z0-9_-]
)

# uploaded.bucket + uploaded.object_key identify the file from now on (not the original file name).
```

File objects are streamed, not read into memory. When `content_type` is omitted it is guessed from the file name.
Allowed content types and the maximum size are set per CDN deployment (commonly PDF, JPEG, PNG, WEBP, DOC and DOCX,
up to about 25 MB). Anything outside them fails with `40001`.

### Give a browser or mobile app access

| The file is in... | Use | Makes an HTTP call? | Link lifetime |
|---|---|:---:|---|
| a **Public** bucket | `cdn.build_public_url(bucket, object_key)` | no | permanent |
| a **Private** bucket | `cdn.create_presigned_url(bucket, object_key, expiry_seconds=600)` | yes | short (you choose) |
| many private files | `cdn.create_presigned_urls([(bucket, key), ...], expiry_seconds=600)` | yes, once | short |

```python
logo_url = cdn.build_public_url("public-assets", object_key)

link = cdn.create_presigned_url("documents", object_key, expiry_seconds=600)
print(link.url, link.expires_at_utc)

batch = cdn.create_presigned_urls([("documents", key_a), ("documents", key_b)], expiry_seconds=600)
for failed in batch.failed:          # batch returns 200 even if some targets fail
    log.warning("%s: %s", failed.object_key, failed.reason)
```

Omit `expiry_seconds` to use the server default; the server clamps it to its configured maximum.

> Never send the API key to a browser or mobile app. Give them a public or presigned URL instead.

### Download (backend to backend)

```python
with cdn.download("documents", object_key) as download:
    print(download.content_type, download.file_name, download.size_bytes)
    for chunk in download.iter_bytes():
        sink.write(chunk)

written = cdn.download_to("documents", object_key, "/tmp/report.pdf")  # path or binary file object

with cdn.download("documents", object_key) as download:                # small files only
    data = download.read()
```

Stream straight through a web framework without loading the file into memory, for example with FastAPI:

```python
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from ninjavault_cdn import AsyncNinjaVaultCdnClient

app = FastAPI()
cdn = AsyncNinjaVaultCdnClient()

@app.get("/files/{object_key:path}")
async def get_file(object_key: str) -> StreamingResponse:
    download = await cdn.download("documents", object_key)

    async def body():
        try:
            async for chunk in download.aiter_bytes():
                yield chunk
        finally:
            await download.aclose()

    return StreamingResponse(
        body(),
        media_type=download.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{download.file_name or "file"}"'},
    )
```

With the async client, use either `async with cdn.download(...) as download:` or
`download = await cdn.download(...)` and close it yourself with `await download.aclose()`.

### Search and list

```python
from datetime import datetime, timedelta, timezone
from ninjavault_cdn import FileCategory, FileListQuery, FileSortBy

page = cdn.list_files(FileListQuery(
    bucket="documents",
    tenant_id=42,
    owner_id=1001,
    object_key_prefix="reports/2026",
    file_name_contains="invoice",
    category=FileCategory.DOCUMENT,
    created_from_utc=datetime.now(timezone.utc) - timedelta(days=30),
    sort_by=FileSortBy.CREATED_AT_UTC,
    sort_descending=True,
    page=1,
    page_size=50,                    # max 200
))

page = cdn.list_files(bucket="documents", page=2)   # keyword filters work too

for item in page.items:
    print(item.object_key, item.size_bytes, item.created_at_utc)
```

### Metadata, usage, buckets, delete

```python
info = cdn.get_metadata("documents", object_key)   # checksum, category, thumbnail...
usage = cdn.get_summary("documents")               # totals per category (omit the bucket for all)
buckets = cdn.list_buckets()                       # visibility + counters
cdn.delete("documents", object_key)                # soft delete
```

---

## Error handling

```python
import time
from ninjavault_cdn import CdnApiError, CdnConnectionError, ErrorCode

try:
    cdn.upload("documents", 42, data, "report.pdf")
except CdnApiError as ex:
    if ex.error_code == ErrorCode.TOO_MANY_REQUESTS and ex.retry_after is not None:
        time.sleep(ex.retry_after)            # back off exactly as long as the server asks
    else:
        log.warning("CDN %s %s: %s (trace %s)", ex.status_code, ex.error_code, ex.message, ex.correlation_id)
        for field, errors in (ex.details or {}).items():
            log.warning("%s: %s", field, ", ".join(errors))
except CdnConnectionError:                    # DNS, TLS, refused, timeouts (CdnTimeoutError)
    log.exception("CDN unreachable")
```

| `CdnApiError` attribute | Meaning |
|---|---|
| `status_code` | HTTP status code |
| `error_code` | CDN error code (compare with `ErrorCode`), `None` if the body was not a CDN envelope |
| `message` / `description` | Error text; `description` holds the raw body when the server did not send JSON |
| `correlation_id` | Server trace id (`traceId`). Send it to the CDN team. |
| `details` | Field-level validation errors, `dict[str, list[str]]` |
| `retry_after` | Seconds from `Retry-After` (delta or HTTP-date), mainly on 429 |

| `ErrorCode` | HTTP | Meaning | What to check |
|---|:---:|---|---|
| `40001` `VALIDATION_FAILED` | 400 | Validation failed | `folder_path` format, content type, file size, `ex.details` |
| `40101` `UNAUTHORIZED` | 401 | Unauthorized | API key missing, mistyped, or revoked |
| `40301` `FORBIDDEN` | 403 | Forbidden | Key not allowed for this bucket or tenant (`get_access_context()`) |
| `40401` `NOT_FOUND` | 404 | Not found | Bucket name or object key (use `object_key`, not the file name) |
| `40901` `CONFLICT` | 409 | Conflict | Quota exceeded or conflicting state |
| `42901` `TOO_MANY_REQUESTS` | 429 | Too many requests | Wait `ex.retry_after` seconds |
| `50001` `INTERNAL_SERVER_ERROR` | 500 | Server error | Retry later; send `ex.correlation_id` to the CDN team |

All SDK exceptions derive from `NinjaVaultCdnError`. `CdnConnectionError` and `CdnTimeoutError` wrap the original
`httpx` exception (available as `__cause__`). Invalid arguments (blank bucket or object key, missing file name)
raise `ValueError` / `TypeError` before any request is sent.

---

## Logging, correlation and retries

The SDK logs one DEBUG line per request on the `ninjavault_cdn` logger (method, URL, status, elapsed ms). It never
logs the API key, headers or file bytes, and it adds no handlers: whether and where these lines go is your app's
choice.

```python
import logging
logging.getLogger("ninjavault_cdn").setLevel(logging.DEBUG)
```

For your own request logging, metrics or an `X-Correlation-Id` header, use `httpx` event hooks:

```python
import uuid

def add_correlation_id(request):
    request.headers["X-Correlation-Id"] = current_correlation_id() or str(uuid.uuid4())

def log_response(response):
    log.info("CDN %s %s -> %s", response.request.method, response.request.url, response.status_code)

cdn = NinjaVaultCdnClient(event_hooks={"request": [add_correlation_id], "response": [log_response]})
```

With `AsyncNinjaVaultCdnClient`, hooks must be `async def`. If you pass your own `http_client`, configure hooks,
proxies, retries (for example `httpx.HTTPTransport(retries=3)`) on it instead; passing both `http_client` and
`event_hooks` raises `ValueError`.

> Every request carries the `X-Api-Key` header. If your hook logs headers, redact it first.

---

### Retries

The SDK never retries on its own, so a retry can't upload a file twice behind your back. Two easy options:

```python
import httpx
from ninjavault_cdn import NinjaVaultCdnClient

# Retry failed connections (DNS, refused, TLS) up to 3 times
cdn = NinjaVaultCdnClient(http_client=httpx.Client(transport=httpx.HTTPTransport(retries=3), timeout=30))
```

For rate limits (`42901`), wait `ex.retry_after` seconds and try again, as in the error-handling example above.

---

## Unit testing your code

Swap the transport with `httpx.MockTransport` to exercise your code without a network:

```python
import httpx
from ninjavault_cdn import NinjaVaultCdnClient

def handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["X-Api-Key"] == "cdn_test"
    return httpx.Response(200, json={"success": True, "data": [], "error": None})

cdn = NinjaVaultCdnClient(
    base_url="https://cdn.test",
    api_key="cdn_test",
    http_client=httpx.Client(transport=httpx.MockTransport(handler)),
)
assert cdn.list_buckets() == []
```

Or depend on the client in your own code and mock it:

```python
from unittest.mock import create_autospec
from ninjavault_cdn import NinjaVaultCdnClient

cdn = create_autospec(NinjaVaultCdnClient, instance=True)
cdn.build_public_url.return_value = "https://cdn.test/public/public-assets/logo.png"
service = BrandingService(cdn)
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Public URLs contain `/public/public/` | Set `public_base_url` to the host only (`https://cdn.example.com`). This SDK also accepts a value ending in `/public` and does not repeat it. |
| `ValueError: NinjaVault CDN base_url is not configured` (or `api_key`) | Pass the argument or set `NINJAVAULT_CDN_BASE_URL` / `NINJAVAULT_CDN_API_KEY` in the process environment. |
| `40101` on every call | Wrong or rotated key. Check `NINJAVAULT_CDN_API_KEY`. |
| `40001` on upload | Invalid `folder_path` (max 8 segments of `[A-Za-z0-9_-]`, each starting with a letter or digit), or a file type/size the deployment doesn't allow. See `ex.details`. |
| `40301` for a bucket that exists | The key is scoped to other buckets or tenants. Inspect `get_access_context()`. |
| File not found after upload | Fetch with the returned `object_key`, not the original file name. |
| `CdnTimeoutError` on large uploads/downloads | Raise `timeout`, e.g. `httpx.Timeout(30, read=300, write=300)`. |
| `RuntimeWarning: coroutine ... was never awaited` | With the async client, `await` the call or use `async with cdn.download(...)`. |

---

## Compatibility

| | |
|---|---|
| Python | 3.10, 3.11, 3.12, 3.13, 3.14 |
| Dependencies | `httpx>=0.27,<1` (nothing else) |
| Typing | Ships `py.typed`; checked with `mypy --strict` |
| Server | NinjaVault CDN Server `/api/v1` API-key routes |

## NinjaVault SDKs

The same CDN client in every language, with the same features and the **same version number** (for example `100.42.1` everywhere):

| Language | Package | Install | Source |
|---|---|---|---|
| .NET 8+ | [`NinjaVault.Cdn`](https://www.nuget.org/packages/NinjaVault.Cdn) | `dotnet add package NinjaVault.Cdn` | [NinjaVault](https://github.com/alisaivi786/NinjaVault) |
| Python 3.10+ | [`ninjavault-cdn`](https://pypi.org/project/ninjavault-cdn/) | `pip install ninjavault-cdn` | [NinjaVault-Python](https://github.com/alisaivi786/NinjaVault-Python) |
| Node.js 18+ | [`@ninjavault/cdn`](https://www.npmjs.com/package/@ninjavault/cdn) | `npm install @ninjavault/cdn` | [NinjaVault-Node](https://github.com/alisaivi786/NinjaVault-Node) |

---

## Links

- Source and issues: <https://github.com/alisaivi786/NinjaVault-Python>
- Release notes (change-sets): <https://github.com/alisaivi786/NinjaVault-Python/tree/main/changesets>
- Releasing: [docs/RELEASING.md](https://github.com/alisaivi786/NinjaVault-Python/blob/main/docs/RELEASING.md)
- Security: [SECURITY.md](https://github.com/alisaivi786/NinjaVault-Python/blob/main/SECURITY.md)
- License: MIT
