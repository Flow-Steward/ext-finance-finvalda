"""Step and size limits shared by every runner."""

from __future__ import annotations

#: The entrypoint is given 600 s (extension.yaml). Every request must start early
#: enough to finish inside it; a step that saves a file keeps the artifact write's
#: own timeout in reserve, so it can always save what it read.
STEP_TIMEOUT_SECONDS = 600.0

STEP_MARGIN_SECONDS = 30.0

ARTIFACT_WRITE_TIMEOUT_SECONDS = 120.0

DEFAULT_LIMIT = 500

MAX_LIMIT = 1000

DEFAULT_MAX_PAGES = 10

MAX_PAGES = 100

EXPORT_PAGE = 1000

EXPORT_MAX_PAGES = 2000

#: Files travel hex-encoded (two characters a byte) inside one JSON body of at most
#: 20 MB (transport.MAX_REQUEST_BODY_BYTES), so 8 MB is the most that fits.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024

MAX_DOWNLOAD_BYTES = 40 * 1024 * 1024

MAX_EXPORT_BYTES = 200 * 1024 * 1024

MAX_TEXT = 1000

MAX_LIST_ITEMS = 1000

#: Must equal the content types granted in contracts/artifact_policies.yaml.
DOWNLOAD_CONTENT_TYPE = "application/octet-stream"

EXPORT_CONTENT_TYPE = "application/x-ndjson"

EXPORT_FILENAME = "finvalda-export.jsonl"
