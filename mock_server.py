"""Mock server that fails N times then succeeds, plus a 4xx endpoint."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

app = FastAPI(title="Mock External Service")

# Track call counts per-path to simulate transient failures
call_counts: dict[str, int] = {}


@app.post("/unstable")
async def unstable_endpoint() -> dict[str, str]:
    """Fails with 500 three times, then returns 200."""
    count = call_counts.get("unstable", 0) + 1
    call_counts["unstable"] = count
    if count <= 3:
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error (call #{count})",
        )
    return {"status": "success", "message": f"Succeeded on attempt {count}"}


@app.post("/always-fail")
async def always_fail() -> dict[str, str]:
    """Always returns 500 — will exhaust retries and be dead-lettered."""
    count = call_counts.get("always-fail", 0) + 1
    call_counts["always-fail"] = count
    raise HTTPException(
        status_code=500,
        detail=f"Internal server error (call #{count})",
    )


@app.post("/bad-request")
async def bad_request() -> dict[str, str]:
    """Returns 400 — should NOT be retried."""
    raise HTTPException(status_code=400, detail="Bad request — invalid payload")


@app.post("/reset")
async def reset() -> dict[str, str]:
    """Reset call counters for re-running tests."""
    call_counts.clear()
    return {"status": "reset"}
