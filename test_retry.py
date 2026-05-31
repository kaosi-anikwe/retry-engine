"""
Test script: submits three requests to the retry engine and polls results.

1. /unstable  — fails 3 times, then succeeds (shows backoff doubling + jitter)
2. /bad-request — returns 400 (should NOT be retried)
3. /always-fail — always 500 (should be dead-lettered after maxRetries)

Usage:
    1. Start mock server:  uv run uvicorn mock_server:app --port 9000
    2. Start retry engine: uv run uvicorn app.main:app --port 8000
    3. Run this script:    uv run python test_retry.py
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

import httpx

ENGINE_URL = "http://localhost:8000"
MOCK_URL = "http://localhost:9000"


def submit(name: str, path: str, max_retries: int = 5) -> str:
    """Submit a request to the retry engine and return its ID."""
    payload = {
        "url": f"{MOCK_URL}{path}",
        "method": "POST",
        "body": json.dumps({"test": name}),
        "maxRetries": max_retries,
        "backoffMs": 1000,
    }
    resp = httpx.post(f"{ENGINE_URL}/request", json=payload)
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    print(f"[{name}] Submitted → id={data['id'][:8]}... status={data['status']}")
    return str(data["id"])


def poll(request_id: str, label: str, timeout: int = 60) -> dict[str, Any]:
    """Poll until the request reaches a terminal state."""
    start = time.time()
    while time.time() - start < timeout:
        resp = httpx.get(f"{ENGINE_URL}/requests/{request_id}")
        data: dict[str, Any] = resp.json()
        status = data["status"]
        attempts = len(data.get("attempts", []))
        if status in ("completed", "failed"):
            print(f"[{label}] Final: status={status}, attempts={attempts}")
            return data
        print(f"[{label}] Polling: status={status}, attempts={attempts}")
        time.sleep(2)
    print(f"[{label}] Timed out after {timeout}s")
    return {}


def print_result(label: str, data: dict[str, Any]) -> None:
    """Print a summary of the request result."""
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")
    print(f"  Status : {data['status']}")
    print(f"  Attempts: {data['attempt_count']}")
    if data.get("result"):
        print(f"  Result : {data['result'][:100]}")
    if data.get("last_error"):
        print(f"  Error  : {data['last_error'][:100]}")
    for a in data.get("attempts", []):
        code = a.get("status_code") or "ERR"
        err = a.get("error") or ""
        print(
            f"    #{a['attempt_number']}: HTTP {code}  ({a['duration_ms']:.0f}ms) {err}"
        )
    print()


def main() -> None:
    # Reset mock server counters
    try:
        httpx.post(f"{MOCK_URL}/reset")
    except httpx.ConnectError:
        print("ERROR: Mock server not running on port 9000", file=sys.stderr)
        sys.exit(1)

    try:
        httpx.get(f"{ENGINE_URL}/requests")
    except httpx.ConnectError:
        print("ERROR: Retry engine not running on port 8000", file=sys.stderr)
        sys.exit(1)

    print("\n--- Submitting test requests ---\n")

    # 1. Unstable endpoint: fails 3x then succeeds
    id1 = submit("unstable", "/unstable")

    # 2. Bad request: 400 → should NOT be retried
    id2 = submit("bad-request", "/bad-request")

    # 3. Always fail: dead-letter after maxRetries
    id3 = submit("always-fail", "/always-fail", max_retries=3)

    print("\n--- Waiting for results ---\n")

    r1 = poll(id1, "unstable")
    r2 = poll(id2, "bad-request")
    r3 = poll(id3, "always-fail")

    print_result("UNSTABLE (should succeed after 4 attempts)", r1)
    print_result("BAD REQUEST (should fail immediately, 1 attempt)", r2)
    print_result("ALWAYS FAIL (should be dead-lettered after 3 attempts)", r3)

    # Show filtering by status
    print("--- Filtering by status=failed ---\n")
    resp = httpx.get(f"{ENGINE_URL}/requests", params={"status": "failed"})
    failed = resp.json()
    for r in failed:
        print(f"  {r['id'][:8]}... → {r['status']} ({r['attempt_count']} attempts)")

    print("\nDone!")


if __name__ == "__main__":
    main()
