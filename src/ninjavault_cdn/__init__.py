"""Typed Python client for the NinjaVault CDN Server.

>>> from ninjavault_cdn import NinjaVaultCdnClient
>>> with NinjaVaultCdnClient() as cdn:  # reads NINJAVAULT_CDN_BASE_URL / NINJAVAULT_CDN_API_KEY
...     uploaded = cdn.upload("documents", 42, b"%PDF-...", "invoice.pdf", "application/pdf")
"""

from ._async_client import AsyncNinjaVaultCdnClient
from ._client import NinjaVaultCdnClient
from ._download import AsyncFileDownload, FileDownload
from ._enums import (
    BucketVisibility,
    ErrorCode,
    FileCategory,
    FileSortBy,
    PresignFailureReason,
    ThumbnailStatus,
)
from ._errors import CdnApiError, CdnConnectionError, CdnTimeoutError, NinjaVaultCdnError
from ._models import (
    AccessBucket,
    AccessContext,
    Bucket,
    FileCategorySummary,
    FileListQuery,
    FileObject,
    FileSummary,
    PagedResult,
    PresignBatchResult,
    PresignedTarget,
    PresignedUrl,
    PresignFailure,
    PresignTarget,
    UploadResult,
)
from ._version import __version__

__all__ = [
    "AccessBucket",
    "AccessContext",
    "AsyncFileDownload",
    "AsyncNinjaVaultCdnClient",
    "Bucket",
    "BucketVisibility",
    "CdnApiError",
    "CdnConnectionError",
    "CdnTimeoutError",
    "ErrorCode",
    "FileCategory",
    "FileCategorySummary",
    "FileDownload",
    "FileListQuery",
    "FileObject",
    "FileSortBy",
    "FileSummary",
    "NinjaVaultCdnClient",
    "NinjaVaultCdnError",
    "PagedResult",
    "PresignBatchResult",
    "PresignFailure",
    "PresignFailureReason",
    "PresignTarget",
    "PresignedTarget",
    "PresignedUrl",
    "ThumbnailStatus",
    "UploadResult",
    "__version__",
]
