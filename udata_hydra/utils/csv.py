import json


async def detect_csv_from_headers(check) -> bool:
    """
    Determine if content-type header looks like a csv's one
    or if it's binary for potential csv.gz
    """
    headers = json.loads(check["headers"] or "{}")
    return any(
        headers.get("content-type", "").lower().startswith(ct) for ct in [
            "application/csv", "text/plain", "text/csv"
        ]
    ), any([
        headers.get("content-type", "").lower().startswith(ct) for ct in [
            "application/octet-stream", "application/x-gzip"
        ]
    ])
