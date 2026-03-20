"""Settings routes for Axion API.

Provides configuration endpoints for operator use — specifically
API key management from the dashboard Settings UI.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.config import get_settings, DEFAULT_ENV_PATH

logger = logging.getLogger("axion.settings")

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Settings are written to the user-scoped Axion config file (~/.axion.env).
# This survives app updates/reinstalls and is the primary env file in the
# config loading hierarchy.
_ENV_FILE = DEFAULT_ENV_PATH  # ~/.axion.env


def _mask_key(key: str) -> str:
    """Return a masked version of an API key for safe display."""
    if not key or len(key) < 12:
        return "****"
    return key[:7] + "****" + key[-4:]


def _read_env_lines() -> list[str]:
    """Read the Axion env file, returning lines (or empty list)."""
    if _ENV_FILE.exists():
        return _ENV_FILE.read_text(encoding="utf-8").splitlines(keepends=True)
    return []


def _write_env_key(var_name: str, value: str) -> None:
    """Set or update a key=value pair in the user Axion env file (~/.axion.env).

    Creates the file if it doesn't exist. If the variable already exists
    (commented or not), it is updated in place. Otherwise a new line is appended.
    """
    lines = _read_env_lines()

    # If the file doesn't exist yet, seed it with a header
    if not lines:
        lines = [
            "# Axion by 4Labs — User Configuration\n",
            "# This file is managed by the Axion Settings UI.\n",
            "# It can also be edited manually.\n",
            "\n",
        ]

    pattern = re.compile(
        rf"^#?\s*{re.escape(var_name)}\s*=", re.IGNORECASE
    )
    found = False
    new_lines: list[str] = []
    for line in lines:
        if pattern.match(line.rstrip("\n\r")):
            new_lines.append(f"{var_name}={value}\n")
            found = True
        else:
            new_lines.append(line if line.endswith("\n") else line + "\n")
    if not found:
        new_lines.append(f"{var_name}={value}\n")
    _ENV_FILE.write_text("".join(new_lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ApiKeyStatus(BaseModel):
    configured: bool
    masked_key: str | None = None
    source: str | None = None  # "env_var", "env_file", "not_set"
    llm_available: bool = False


class ApiKeySaveRequest(BaseModel):
    api_key: str


class ApiKeySaveResponse(BaseModel):
    status: str  # "saved", "error"
    message: str
    restart_required: bool = True


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/api-key", response_model=ApiKeyStatus)
async def get_api_key_status():
    """Return whether an Anthropic API key is configured (masked)."""
    from src.llm.client import is_llm_available

    settings = get_settings()
    raw_key = settings.anthropic_api_key.get_secret_value()

    if raw_key:
        # Determine source
        env_val = os.environ.get("ANTHROPIC_API_KEY", "")
        source = "env_var" if env_val else "env_file"
        return ApiKeyStatus(
            configured=True,
            masked_key=_mask_key(raw_key),
            source=source,
            llm_available=is_llm_available(),
        )
    return ApiKeyStatus(
        configured=False,
        masked_key=None,
        source="not_set",
        llm_available=False,
    )


@router.post("/api-key", response_model=ApiKeySaveResponse)
async def save_api_key(req: ApiKeySaveRequest):
    """Save an Anthropic API key to the user config file (~/.axion.env).

    The key is validated for basic format (must start with ``sk-ant-``
    and be at least 20 characters). The application must be restarted
    for the change to take effect.
    """
    key = req.api_key.strip()

    # Basic format validation
    if not key:
        raise HTTPException(status_code=400, detail="API key cannot be empty.")
    if not key.startswith("sk-ant-"):
        raise HTTPException(
            status_code=400,
            detail="Invalid key format. Anthropic API keys start with 'sk-ant-'.",
        )
    if len(key) < 20:
        raise HTTPException(
            status_code=400,
            detail="Key is too short. Please check your Anthropic API key.",
        )

    try:
        _write_env_key("ANTHROPIC_API_KEY", key)
        logger.info("Anthropic API key saved to %s", _ENV_FILE)
        return ApiKeySaveResponse(
            status="saved",
            message="API key saved. Restart Axion to activate AI-enhanced analysis.",
            restart_required=True,
        )
    except Exception as e:
        logger.error("Failed to save API key: %s", e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to write config file: {e}",
        )


@router.post("/quit-signal")
async def write_quit_signal():
    """Write a quit signal file that the desktop shell watches for.

    This tells the Axion app shell to shut down gracefully.
    """
    data_dir = Path.home() / "kleitos-data"
    signal_file = data_dir / ".quit-app"
    try:
        signal_file.write_text("quit", encoding="utf-8")
        logger.info("Quit signal written to %s", signal_file)
        return {"status": "ok", "message": "Quit signal sent."}
    except Exception as e:
        logger.error("Failed to write quit signal: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api-key", response_model=ApiKeySaveResponse)
async def remove_api_key():
    """Remove the Anthropic API key from the user config file (~/.axion.env)."""
    try:
        _write_env_key("ANTHROPIC_API_KEY", "")
        # Actually comment it out instead of leaving empty
        lines = _read_env_lines()
        new_lines = []
        for line in lines:
            if line.strip().startswith("ANTHROPIC_API_KEY=") and line.strip() == "ANTHROPIC_API_KEY=":
                new_lines.append("# ANTHROPIC_API_KEY=\n")
            else:
                new_lines.append(line)
        _ENV_FILE.write_text("".join(new_lines), encoding="utf-8")

        logger.info("Anthropic API key removed from %s", _ENV_FILE)
        return ApiKeySaveResponse(
            status="saved",
            message="API key removed. Restart Axion to switch to rule-based mode.",
            restart_required=True,
        )
    except Exception as e:
        logger.error("Failed to remove API key: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to update .env file: {e}")
