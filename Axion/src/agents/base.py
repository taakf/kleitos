"""Base agent class for the Axion portfolio intelligence system.

Every domain agent inherits from BaseAgent, which provides:
- Permission checks (read/write allowlists per table)
- Structured run lifecycle logging (start / complete / error)
- Audit-log helper
- Database session helper
"""

from __future__ import annotations

import json
import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, ClassVar

from src.database.models import AgentRun, AuditLog
from src.database.connection import get_db

logger = logging.getLogger(__name__)


class AgentPermissionError(Exception):
    """Raised when an agent attempts an operation it is not allowed to perform."""


class BaseAgent(ABC):
    """Abstract base for every Axion domain agent.

    Subclasses **must** override:
        * ``agent_name``  (class-level str)
        * ``read_permissions`` / ``write_permissions`` (class-level lists)
        * ``run()``  (the agent's main entry point)
    """

    # -- subclass overrides ------------------------------------------------
    agent_name: ClassVar[str] = "base"
    read_permissions: ClassVar[list[str]] = []
    write_permissions: ClassVar[list[str]] = []

    def __init__(self) -> None:
        self.agent_id: str = str(uuid.uuid4())
        self._run_id: str | None = None
        logger.info("Initialised %s  agent_id=%s", self.agent_name, self.agent_id)

    # -- abstract ----------------------------------------------------------
    @abstractmethod
    async def run(self, **kwargs: Any) -> Any:
        """Execute the agent's primary task.  Must be implemented by subclasses."""
        ...

    # -- permission helpers ------------------------------------------------
    def _check_permission(self, table: str, operation: str) -> None:
        """Validate that this agent is allowed *operation* on *table*.

        Parameters
        ----------
        table:
            Logical table name (e.g. ``"holdings"``).
        operation:
            Either ``"read"`` or ``"write"``.

        Raises
        ------
        AgentPermissionError
            If the agent's permission lists do not include the table.
        """
        if operation == "read":
            allowed = self.read_permissions
        elif operation == "write":
            allowed = self.write_permissions
        else:
            raise ValueError(f"Unknown operation '{operation}'; expected 'read' or 'write'")

        if table not in allowed:
            msg = (
                f"{self.agent_name} does not have {operation} permission on '{table}'. "
                f"Allowed: {allowed}"
            )
            logger.error(msg)
            raise AgentPermissionError(msg)

    # -- database helper ---------------------------------------------------
    @staticmethod
    def _get_db():
        """Return an async database session context manager."""
        return get_db()

    # -- run lifecycle logging ---------------------------------------------
    async def _log_run_start(self, parameters: dict[str, Any] | None = None) -> str:
        """Record the start of an agent run in the ``agent_runs`` table.

        Returns the generated ``run_id``.
        """
        self._run_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        run_record = AgentRun(
            id=self._run_id,
            agent_id=self.agent_name,
            run_type="scheduled",
            status="running",
            started_at=now,
        )

        async with self._get_db() as session:
            session.add(run_record)
            await session.commit()

        logger.info(
            "[%s] run started  run_id=%s  params=%s",
            self.agent_name,
            self._run_id,
            parameters,
        )
        return self._run_id

    async def _log_run_complete(self, result_summary: dict[str, Any] | None = None) -> None:
        """Mark the current run as successfully completed."""
        if self._run_id is None:
            logger.warning("[%s] _log_run_complete called without a prior _log_run_start", self.agent_name)
            return

        now = datetime.now(timezone.utc).isoformat()

        async with self._get_db() as session:
            run_record = await session.get(AgentRun, self._run_id)
            if run_record:
                run_record.status = "completed"
                run_record.completed_at = now
                await session.commit()

        logger.info(
            "[%s] run completed  run_id=%s  summary=%s",
            self.agent_name,
            self._run_id,
            result_summary,
        )

    async def _log_run_error(self, error: Exception) -> None:
        """Mark the current run as failed and persist the error message.

        Wrapped in a safety try/except so that a DB failure here never
        masks the original error that triggered this call.
        """
        if self._run_id is None:
            logger.warning("[%s] _log_run_error called without a prior _log_run_start", self.agent_name)
            return

        try:
            now = datetime.now(timezone.utc).isoformat()

            async with self._get_db() as session:
                run_record = await session.get(AgentRun, self._run_id)
                if run_record:
                    run_record.status = "failed"
                    run_record.completed_at = now
                    run_record.error_message = str(error)
                    await session.commit()
        except Exception:
            logger.exception("[%s] Failed to persist run error for run_id=%s", self.agent_name, self._run_id)

        logger.error(
            "[%s] run failed  run_id=%s  error=%s",
            self.agent_name,
            self._run_id,
            error,
            exc_info=True,
        )

    # -- audit log ---------------------------------------------------------
    async def _audit_log(
        self,
        action: str,
        entity_type: str,
        entity_id: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Write a row to the ``audit_log`` table.

        Parameters
        ----------
        action:
            Short verb, e.g. ``"created"``, ``"updated"``, ``"classified"``.
        entity_type:
            Logical entity, e.g. ``"holding"``, ``"security"``, ``"event"``.
        entity_id:
            Primary key of the affected entity.
        details:
            Arbitrary JSON-serialisable metadata.
        """
        log_entry = AuditLog(
            id=str(uuid.uuid4()),
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            new_value=json.dumps(details) if details else None,
            agent_id=self.agent_name,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        async with self._get_db() as session:
            session.add(log_entry)
            await session.commit()

        logger.debug(
            "[%s] audit  action=%s  entity=%s/%s",
            self.agent_name,
            action,
            entity_type,
            entity_id,
        )
