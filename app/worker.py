from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.database import get_db
from app.models import RequestStatus

logger = logging.getLogger("retry-engine.worker")

POLL_INTERVAL = 0.5  # seconds
REQUEST_TIMEOUT = 10.0  # seconds


def _compute_next_retry_at(backoff_ms: int, attempt_count: int) -> str:
    """Compute the next retry time with exponential backoff and jitter."""
    base_delay_ms = backoff_ms * (2 ** (attempt_count - 1))
    jitter = random.uniform(0.8, 1.2)
    delay_ms = base_delay_ms * jitter
    next_time = datetime.now(UTC) + timedelta(milliseconds=delay_ms)
    logger.info(
        "  Backoff: base=%dms * jitter=%.3f = %dms (next retry at %s)",
        base_delay_ms,
        jitter,
        delay_ms,
        next_time.strftime("%H:%M:%S.%f")[:-3],
    )
    return next_time.strftime("%Y-%m-%d %H:%M:%S.%f")


def _is_retryable(status_code: int | None, error: str | None) -> bool:
    """5xx, timeouts, and network errors are retryable. 4xx is not."""
    if error is not None:
        return True
    if status_code is not None and status_code >= 500:
        return True
    return False


async def _execute_request(
    url: str, method: str, body: str | None
) -> tuple[int | None, str | None, str | None]:
    """Execute a single HTTP request. Returns (status_code, response_body, error)."""
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.request(
                method=method,
                url=url,
                content=body,
                headers={"Content-Type": "application/json"} if body else None,
            )
            return response.status_code, response.text, None
    except httpx.TimeoutException as e:
        return None, None, f"Timeout: {e}"
    except httpx.ConnectError as e:
        return None, None, f"Connection error: {e}"
    except httpx.HTTPError as e:
        return None, None, f"HTTP error: {e}"


async def _process_request(request_id: str) -> None:
    """Process a single due request: execute it and update the database."""
    db = await get_db()
    try:
        row: list[Any] = list(
            await db.execute_fetchall(
                "SELECT * FROM requests WHERE id = ?", (request_id,)
            )
        )
        if not row:
            return
        req = row[0]

        url: str = req["url"]
        method: str = req["method"]
        body: str | None = req["body"]
        max_retries: int = req["max_retries"]
        backoff_ms: int = req["backoff_ms"]
        attempt_count: int = req["attempt_count"]

        new_attempt = attempt_count + 1
        logger.info(
            "Attempt %d/%d for request %s → %s %s",
            new_attempt,
            max_retries,
            request_id[:8],
            method,
            url,
        )

        start = time.monotonic()
        status_code, response_body, error = await _execute_request(url, method, body)
        duration_ms = (time.monotonic() - start) * 1000

        # Record the attempt
        await db.execute(
            """INSERT INTO attempts (request_id, attempt_number, status_code,
               response_body, error, duration_ms)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (request_id, new_attempt, status_code, response_body, error, duration_ms),
        )

        if error:
            logger.info("  Result: error — %s", error)
        else:
            logger.info("  Result: HTTP %s", status_code)

        # Determine outcome
        if status_code is not None and status_code < 400:
            # Success
            await db.execute(
                """UPDATE requests
                   SET status = ?, attempt_count = ?, result = ?,
                       last_error = NULL, next_retry_at = NULL,
                       updated_at = datetime('now')
                   WHERE id = ?""",
                (RequestStatus.COMPLETED, new_attempt, response_body, request_id),
            )
            logger.info("  → Request COMPLETED")
        elif not _is_retryable(status_code, error):
            # 4xx — terminal, do not retry
            err_msg = f"HTTP {status_code}: {response_body}"
            await db.execute(
                """UPDATE requests
                   SET status = ?, attempt_count = ?, last_error = ?,
                       next_retry_at = NULL, updated_at = datetime('now')
                   WHERE id = ?""",
                (RequestStatus.FAILED, new_attempt, err_msg, request_id),
            )
            logger.info("  → Request FAILED (4xx, not retryable)")
        elif new_attempt >= max_retries:
            # Dead-letter
            err_msg = error or f"HTTP {status_code}: {response_body}"
            await db.execute(
                """UPDATE requests
                   SET status = ?, attempt_count = ?, last_error = ?,
                       next_retry_at = NULL, updated_at = datetime('now')
                   WHERE id = ?""",
                (RequestStatus.FAILED, new_attempt, err_msg, request_id),
            )
            logger.info("  → Request DEAD-LETTERED (max retries reached)")
        else:
            # Retryable failure — schedule next retry
            err_msg = error or f"HTTP {status_code}: {response_body}"
            next_retry = _compute_next_retry_at(backoff_ms, new_attempt)
            await db.execute(
                """UPDATE requests
                   SET status = ?, attempt_count = ?, last_error = ?,
                       next_retry_at = ?, updated_at = datetime('now')
                   WHERE id = ?""",
                (RequestStatus.RETRYING, new_attempt, err_msg, next_retry, request_id),
            )

        await db.commit()
    finally:
        await db.close()


async def worker_loop() -> None:
    """Main worker loop. Polls for due requests every ~500ms."""
    logger.info("Worker started (polling every %.0fms)", POLL_INTERVAL * 1000)
    while True:
        try:
            db = await get_db()
            try:
                now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
                rows: list[Any] = list(
                    await db.execute_fetchall(
                        """SELECT id FROM requests
                           WHERE status IN (?, ?)
                             AND (next_retry_at IS NULL OR next_retry_at <= ?)
                           ORDER BY next_retry_at ASC""",
                        (RequestStatus.PENDING, RequestStatus.RETRYING, now),
                    )
                )
            finally:
                await db.close()

            for row in rows:
                await _process_request(row["id"])

        except Exception:
            logger.exception("Worker error")

        await asyncio.sleep(POLL_INTERVAL)
