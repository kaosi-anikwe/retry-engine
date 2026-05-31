from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query

from app.database import get_db, init_db
from app.models import AttemptOut, RequestCreate, RequestOut, RequestResponse
from app.worker import worker_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    await init_db()
    task = asyncio.create_task(worker_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Retry Engine", lifespan=lifespan)


@app.post("/request", status_code=201)
async def create_request(payload: RequestCreate) -> RequestResponse:
    request_id = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO requests (id, url, method, body, max_retries, backoff_ms)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                request_id,
                payload.url,
                payload.method,
                payload.body_as_str(),
                payload.max_retries,
                payload.backoff_ms,
            ),
        )
        await db.commit()
    finally:
        await db.close()

    return RequestResponse(id=request_id, status="pending")


@app.get("/requests/{request_id}")
async def get_request(request_id: str) -> RequestOut:
    db = await get_db()
    try:
        rows: list[Any] = list(
            await db.execute_fetchall(
                "SELECT * FROM requests WHERE id = ?", (request_id,)
            )
        )
        if not rows:
            raise HTTPException(status_code=404, detail="Request not found")
        req = rows[0]

        attempt_rows: list[Any] = list(
            await db.execute_fetchall(
                "SELECT * FROM attempts WHERE request_id = ? ORDER BY attempt_number",
                (request_id,),
            )
        )
    finally:
        await db.close()

    attempts = [
        AttemptOut(
            attempt_number=a["attempt_number"],
            status_code=a["status_code"],
            response_body=a["response_body"],
            error=a["error"],
            started_at=a["started_at"],
            duration_ms=a["duration_ms"],
        )
        for a in attempt_rows
    ]

    return RequestOut(
        id=req["id"],
        url=req["url"],
        method=req["method"],
        body=req["body"],
        max_retries=req["max_retries"],
        backoff_ms=req["backoff_ms"],
        status=req["status"],
        attempt_count=req["attempt_count"],
        next_retry_at=req["next_retry_at"],
        last_error=req["last_error"],
        result=req["result"],
        created_at=req["created_at"],
        updated_at=req["updated_at"],
        attempts=attempts,
    )


@app.get("/requests")
async def list_requests(
    status: str | None = Query(default=None),
) -> list[RequestOut]:
    db = await get_db()
    try:
        if status:
            rows: list[Any] = list(
                await db.execute_fetchall(
                    "SELECT * FROM requests WHERE status = ? ORDER BY created_at DESC",
                    (status,),
                )
            )
        else:
            rows = list(
                await db.execute_fetchall(
                    "SELECT * FROM requests ORDER BY created_at DESC"
                )
            )

        results: list[RequestOut] = []
        for req in rows:
            attempt_rows: list[Any] = list(
                await db.execute_fetchall(
                    "SELECT * FROM attempts"
                    " WHERE request_id = ? ORDER BY attempt_number",
                    (req["id"],),
                )
            )
            attempts = [
                AttemptOut(
                    attempt_number=a["attempt_number"],
                    status_code=a["status_code"],
                    response_body=a["response_body"],
                    error=a["error"],
                    started_at=a["started_at"],
                    duration_ms=a["duration_ms"],
                )
                for a in attempt_rows
            ]
            results.append(
                RequestOut(
                    id=req["id"],
                    url=req["url"],
                    method=req["method"],
                    body=req["body"],
                    max_retries=req["max_retries"],
                    backoff_ms=req["backoff_ms"],
                    status=req["status"],
                    attempt_count=req["attempt_count"],
                    next_retry_at=req["next_retry_at"],
                    last_error=req["last_error"],
                    result=req["result"],
                    created_at=req["created_at"],
                    updated_at=req["updated_at"],
                    attempts=attempts,
                )
            )
    finally:
        await db.close()

    return results
