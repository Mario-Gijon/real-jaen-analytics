"""Synchronous test facade around HTTPX ASGITransport with explicit lifespan handling."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import httpx


@asynccontextmanager
async def async_asgi_client(app: Any):
    """Keep one app lifespan open for multi-request workflows and background jobs."""
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
