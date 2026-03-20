"""Shared FastAPI dependencies for Axion API."""

from __future__ import annotations

import logging
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from src.database.connection import get_session_factory

logger = logging.getLogger(__name__)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield an async database session for route handlers.

    The session auto-commits on clean exit and rolls back on exception,
    giving each request a single clean transaction boundary.
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
