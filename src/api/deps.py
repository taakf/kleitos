"""Shared FastAPI dependencies for Axion API."""

from __future__ import annotations

import logging
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from src.database.connection import get_session_factory

logger = logging.getLogger(__name__)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield an async database session for route handlers.

    Route handlers own their transaction boundaries by calling
    ``await session.commit()`` explicitly.  The dependency only
    handles rollback on unhandled exceptions and cleanup on exit.
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
