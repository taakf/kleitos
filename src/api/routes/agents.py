"""Agent management routes for Axion API."""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_session
from src.database.models import AgentRun as AgentRunModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])

# Valid agent IDs and their corresponding agent classes
VALID_AGENTS = {"collection", "analysis", "classification", "coverage_qa", "risk", "intake"}


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------
class AgentStatus(BaseModel):
    """Status of a single agent."""

    agent_id: str
    name: str
    status: str  # idle, running, completed, failed
    last_run: str | None
    last_duration_ms: int | None
    run_count: int
    error_count: int


class AgentRunResponse(BaseModel):
    """Record of a single agent execution."""

    id: str
    agent_id: str
    run_type: str
    status: str
    started_at: str
    completed_at: str | None
    items_processed: int
    items_failed: int
    error_message: str | None
    duration_ms: int | None


class AgentRunTriggerResponse(BaseModel):
    run_id: str
    agent_id: str
    status: str
    message: str


# ---------------------------------------------------------------------------
# Background task helper
# ---------------------------------------------------------------------------
async def _run_agent_in_background(agent_id: str) -> None:
    """Instantiate and run the appropriate agent."""
    try:
        if agent_id == "collection":
            from src.agents import CollectionAgent
            agent = CollectionAgent()
        elif agent_id == "analysis":
            from src.agents import AnalysisAgent
            agent = AnalysisAgent()
        elif agent_id == "classification":
            from src.agents import ClassificationAgent
            agent = ClassificationAgent()
        elif agent_id == "coverage_qa":
            from src.agents import CoverageQAAgent
            agent = CoverageQAAgent()
        elif agent_id == "risk":
            from src.agents import RiskAgent
            agent = RiskAgent()
        elif agent_id == "intake":
            from src.agents import IntakeAgent
            agent = IntakeAgent()
        else:
            logger.error("Unknown agent_id for background run: %s", agent_id)
            return

        await agent.run()
    except Exception:
        logger.exception("Background agent run failed for %s", agent_id)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get("/status", response_model=list[AgentStatus])
async def all_agent_status(
    session: AsyncSession = Depends(get_session),
) -> list[AgentStatus]:
    """Return status and last-run info for all agents."""
    # For each agent_id, get the latest run and aggregate counts
    subq_latest = (
        select(
            AgentRunModel.agent_id,
            func.max(AgentRunModel.started_at).label("max_started"),
        )
        .group_by(AgentRunModel.agent_id)
        .subquery()
    )

    # Get latest run details
    stmt_latest = (
        select(AgentRunModel)
        .join(
            subq_latest,
            (AgentRunModel.agent_id == subq_latest.c.agent_id)
            & (AgentRunModel.started_at == subq_latest.c.max_started),
        )
    )
    latest_runs = {
        r.agent_id: r
        for r in (await session.execute(stmt_latest)).scalars().all()
    }

    # Get total run counts and error counts per agent
    stmt_counts = select(
        AgentRunModel.agent_id,
        func.count().label("run_count"),
        func.count()
        .filter(AgentRunModel.status == "failed")
        .label("error_count"),
    ).group_by(AgentRunModel.agent_id)
    count_rows = (await session.execute(stmt_counts)).all()
    counts = {row.agent_id: (row.run_count, row.error_count) for row in count_rows}

    # Build response for every known agent, even those with no runs yet
    all_agents = set(counts.keys()) | set(latest_runs.keys()) | VALID_AGENTS
    result: list[AgentStatus] = []
    for aid in sorted(all_agents):
        latest = latest_runs.get(aid)
        run_count, error_count = counts.get(aid, (0, 0))
        result.append(
            AgentStatus(
                agent_id=aid,
                name=f"{aid.replace('_', ' ').title()} Agent",
                status=latest.status if latest else "idle",
                last_run=latest.started_at if latest else None,
                last_duration_ms=latest.duration_ms if latest else None,
                run_count=run_count,
                error_count=error_count,
            )
        )
    return result


@router.get("/runs", response_model=list[AgentRunResponse])
async def recent_runs(
    agent_id: str | None = None,
    limit: int = Query(20, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[AgentRunResponse]:
    """Return recent agent runs, optionally filtered by agent_id."""
    stmt = select(AgentRunModel).order_by(AgentRunModel.started_at.desc()).limit(limit)
    if agent_id is not None:
        stmt = stmt.where(AgentRunModel.agent_id == agent_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        AgentRunResponse(
            id=r.id,
            agent_id=r.agent_id,
            run_type=r.run_type,
            status=r.status,
            started_at=r.started_at,
            completed_at=r.completed_at,
            items_processed=r.items_processed,
            items_failed=r.items_failed,
            error_message=r.error_message,
            duration_ms=r.duration_ms,
        )
        for r in rows
    ]


@router.post("/{agent_id}/run", response_model=AgentRunTriggerResponse, status_code=202)
async def trigger_agent_run(
    agent_id: str,
    background_tasks: BackgroundTasks,
) -> AgentRunTriggerResponse:
    """Manually trigger an agent run.

    Returns 202 Accepted - the run executes asynchronously.
    """
    if agent_id not in VALID_AGENTS:
        raise HTTPException(status_code=404, detail=f"Unknown agent: {agent_id}")

    run_id = str(uuid.uuid4())
    background_tasks.add_task(_run_agent_in_background, agent_id)

    return AgentRunTriggerResponse(
        run_id=run_id,
        agent_id=agent_id,
        status="accepted",
        message=f"Manual run for {agent_id} agent queued.",
    )
