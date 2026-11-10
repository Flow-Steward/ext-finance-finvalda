"""Health command for the Finvalda extension (no network)."""

from __future__ import annotations

import json


def check_health() -> dict[str, object]:
    from runtime.catalog import FRIENDLY_IDS, OPERATION_IDS, WRITE_OPERATION_IDS
    from runtime.reference_data import CLASSES, OPERATIONS, SOURCES

    return {
        "extension_id": "flowsteward.finvalda",
        "ok": True,
        "status": "healthy",
        "manual_sha256": SOURCES["manual"]["sha256"],
        "postman_sha256": SOURCES["postman"]["sha256"],
        "declared_operations": len(OPERATION_IDS),
        "everyday_operations": len(FRIENDLY_IDS),
        "advanced_operations": len(OPERATIONS),
        "write_operations": len(WRITE_OPERATION_IDS),
        "documented_classes": len(CLASSES),
    }


if __name__ == "__main__":
    print(json.dumps(check_health(), separators=(",", ":"), sort_keys=True))
