"""Settings routes for Axion API.

Provides configuration endpoints for operator use — specifically
API key management from the dashboard Settings UI.
"""

from __future__ import annotations

import logging
import os
import re

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

    # Phase 22 — this file holds AI provider API keys. Restrict it to the
    # owner (0600) so the documented "~/.axion.env with 600 permissions"
    # guarantee is enforced by the code, not merely claimed in the docs.
    # Best-effort: on filesystems / platforms without POSIX modes (e.g.
    # Windows) chmod is a harmless near no-op.
    try:
        os.chmod(_ENV_FILE, 0o600)
    except OSError:
        logger.debug("could not set 0600 permissions on %s", _ENV_FILE)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ProviderStatus(BaseModel):
    provider: str
    configured: bool
    masked_key: str | None = None
    source: str | None = None  # "env_var", "env_file", "not_set"


class ApiKeyStatus(BaseModel):
    configured: bool
    masked_key: str | None = None
    source: str | None = None  # "env_var", "env_file", "not_set"
    llm_available: bool = False
    # Multi-provider fields
    primary_provider: str = ""
    backup_provider: str = ""
    providers: list[ProviderStatus] = []


class ApiKeySaveRequest(BaseModel):
    api_key: str
    provider: str          # "anthropic", "openai", "google" — required
    role: str = "primary"  # "primary", "backup"


class ProviderSelectRequest(BaseModel):
    primary: str = ""    # "anthropic", "openai", "google", or "" (disabled)
    fallback: str = ""   # "anthropic", "openai", "google", or "" (none)


class ApiKeySaveResponse(BaseModel):
    status: str  # "saved", "error"
    message: str
    restart_required: bool = True


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/api-key", response_model=ApiKeyStatus)
async def get_api_key_status():
    """Return status of all configured AI provider keys."""
    from src.llm.client import is_llm_available

    settings = get_settings()

    # Build per-provider status
    _key_attrs = {
        "anthropic": ("anthropic_api_key", "ANTHROPIC_API_KEY"),
        "openai": ("openai_api_key", "OPENAI_API_KEY"),
        "google": ("google_api_key", "GOOGLE_API_KEY"),
    }
    providers: list[ProviderStatus] = []
    for prov_name, (attr, env_var) in _key_attrs.items():
        raw = getattr(settings, attr).get_secret_value()
        if raw:
            source = "env_var" if os.environ.get(env_var, "") else "env_file"
            providers.append(ProviderStatus(
                provider=prov_name,
                configured=True,
                masked_key=_mask_key(raw),
                source=source,
            ))
        else:
            providers.append(ProviderStatus(
                provider=prov_name,
                configured=False,
                masked_key=None,
                source="not_set",
            ))

    # Primary provider key for backward-compatible fields
    # "none" sentinel means AI is explicitly disabled by the user
    primary_name = settings.llm.provider
    ai_disabled = primary_name.lower() == "none"
    display_primary = "" if ai_disabled else primary_name

    primary_attr, primary_env = _key_attrs.get(primary_name, ("anthropic_api_key", "ANTHROPIC_API_KEY"))
    primary_key = getattr(settings, primary_attr).get_secret_value() if not ai_disabled else ""

    any_configured = any(p.configured for p in providers)

    return ApiKeyStatus(
        configured=any_configured and not ai_disabled,
        masked_key=_mask_key(primary_key) if primary_key else None,
        source="env_var" if os.environ.get(primary_env, "") else ("env_file" if primary_key else "not_set"),
        llm_available=is_llm_available(),
        primary_provider=display_primary,
        backup_provider="" if ai_disabled else (settings.llm.backup_provider or ""),
        providers=providers,
    )


_PROVIDER_ENV_VARS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
}

_PROVIDER_PREFIXES = {
    "anthropic": "sk-ant-",
    "openai": "sk-",
    "google": "AIza",
}


@router.post("/api-key", response_model=ApiKeySaveResponse)
async def save_api_key(req: ApiKeySaveRequest):
    """Save an AI provider API key to the user config file (~/.axion.env).

    Supports Anthropic, OpenAI, and Google Gemini providers.
    The application must be restarted for the change to take effect.
    """
    key = req.api_key.strip()
    provider = req.provider.lower()

    if provider not in _PROVIDER_ENV_VARS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider '{provider}'. Supported: anthropic, openai, google.",
        )

    if not key:
        raise HTTPException(status_code=400, detail="API key cannot be empty.")

    expected_prefix = _PROVIDER_PREFIXES.get(provider)
    if expected_prefix and not key.startswith(expected_prefix):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid key format. {provider.title()} API keys typically start with '{expected_prefix}'.",
        )

    if len(key) < 10:
        raise HTTPException(
            status_code=400,
            detail="Key is too short. Please check your API key.",
        )

    env_var = _PROVIDER_ENV_VARS[provider]
    try:
        _write_env_key(env_var, key)
        logger.info("%s API key saved to %s", provider.title(), _ENV_FILE)
        return ApiKeySaveResponse(
            status="saved",
            message=f"{provider.title()} API key saved. Restart Axion to activate.",
            restart_required=True,
        )
    except Exception as e:
        logger.error("Failed to save %s API key: %s", provider, e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to write config file: {e}",
        )


_VALID_PROVIDERS = {"anthropic", "openai", "google"}


@router.post("/provider", response_model=ApiKeySaveResponse)
async def save_provider_selection(req: ProviderSelectRequest):
    """Save AI provider selection to the user config file (~/.axion.env).

    Sets KLEITOS_LLM_PROVIDER and KLEITOS_LLM_BACKUP_PROVIDER.
    The application must be restarted for the change to take effect.
    """
    primary = req.primary.lower().strip()
    fallback = req.fallback.lower().strip()

    if primary and primary not in _VALID_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown primary provider '{primary}'. Supported: anthropic, openai, google.",
        )
    if fallback and fallback not in _VALID_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown fallback provider '{fallback}'. Supported: anthropic, openai, google.",
        )
    if fallback and fallback == primary:
        raise HTTPException(
            status_code=400,
            detail="Fallback provider cannot be the same as primary.",
        )
    if fallback and not primary:
        raise HTTPException(
            status_code=400,
            detail="Cannot set a fallback without a primary provider.",
        )

    try:
        # Write "none" sentinel when AI is disabled so that the value survives
        # config loading (empty strings are ignored by the walrus-operator guard).
        _write_env_key("KLEITOS_LLM_PROVIDER", primary or "none")
        _write_env_key("KLEITOS_LLM_BACKUP_PROVIDER", fallback)
        logger.info(
            "Provider selection saved: primary=%s, fallback=%s → %s",
            primary or "(disabled)", fallback or "(none)", _ENV_FILE,
        )
        return ApiKeySaveResponse(
            status="saved",
            message="Provider selection saved. Restart Axion to activate.",
            restart_required=True,
        )
    except Exception as e:
        logger.error("Failed to save provider selection: %s", e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to write config file: {e}",
        )


@router.post("/quit-signal")
async def write_quit_signal():
    """Write a quit signal file that the desktop shell watches for.

    This tells the Axion app shell to shut down gracefully.
    """
    from src.config import DEFAULT_DATA_DIR
    data_dir = DEFAULT_DATA_DIR
    signal_file = data_dir / ".quit-app"
    try:
        signal_file.write_text("quit", encoding="utf-8")
        logger.info("Quit signal written to %s", signal_file)
        return {"status": "ok", "message": "Quit signal sent."}
    except Exception as e:
        logger.error("Failed to write quit signal: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


_PROVIDER_ALIASES = {
    "anthropic": "anthropic",
    "claude": "anthropic",
    "openai": "openai",
    "chatgpt": "openai",
    "gpt": "openai",
    "gemini": "gemini",
    "google": "gemini",
}


@router.post("/test-provider")
async def test_provider(provider: str | None = None):
    """Test an AI provider with a minimal call and return a typed status.

    Phase 6 contract — the response shape is stable and lives in
    ``src.llm.provider_status.ProviderStatus``. Key fields::

      provider       canonical provider name (anthropic / openai / gemini)
      status         one of: active / disabled / missing_key / invalid_key /
                     quota_issue / unreachable / misconfigured / error
      configured     True if an API key is present (regardless of validity)
      available      True only when status == "active"
      model          the model used for the probe, if known
      message        a one-line customer-facing summary (key fragments scrubbed)
      detail_code    short machine-readable token for branching
      checked_at     ISO-8601 UTC timestamp

    Parameters
    ----------
    provider:
        Optional. If supplied, test this specific provider regardless of
        which one is currently selected as primary. Accepted values:
        anthropic / openai / gemini (and friendly aliases like "chatgpt").
        If omitted, tests the currently-selected primary provider.
    """
    from src.config import get_settings as _gs
    from src.llm.provider_status import (
        build_status,
        scrub_secrets,
        status_from_exception,
    )

    settings = _gs()
    configured_primary = getattr(settings.llm, "provider", "") or ""

    # Resolve which provider to test.
    if provider is None or provider.strip() == "":
        chosen = configured_primary.lower().strip()
    else:
        chosen = provider.lower().strip()

    chosen = _PROVIDER_ALIASES.get(chosen, chosen)

    if not chosen or chosen == "none":
        return build_status(
            provider="",
            status="disabled",
            configured=False,
        ).model_dump()

    if chosen not in ("anthropic", "openai", "gemini"):
        # Invalid provider name → 400 with a clean message; never a 500.
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown provider '{provider}'. "
                "Supported: anthropic, openai, gemini."
            ),
        )

    # Hand off to the provider's typed test_connection.
    try:
        from src.llm.providers import get_provider
        instance = get_provider(chosen)
    except ModuleNotFoundError as exc:
        return status_from_exception(
            provider=chosen,
            exc=exc,
            configured=False,
        ).model_dump()
    except ValueError:
        # get_provider raises ValueError for "none" or unknown names; the
        # name check above already handled those, so this is defensive.
        return build_status(
            provider=chosen,
            status="error",
            configured=False,
            detail_code="unknown_provider",
        ).model_dump()
    except Exception as exc:  # noqa: BLE001
        return status_from_exception(
            provider=chosen,
            exc=exc,
            configured=False,
        ).model_dump()

    try:
        status = await instance.test_connection(provider_name=chosen)
    except Exception as exc:  # noqa: BLE001 — defence-in-depth
        # test_connection itself should never raise (it catches internally),
        # but if a bug slips through we still want a customer-safe response.
        logger.warning(
            "test_connection unexpectedly raised for %s: %s",
            chosen,
            type(exc).__name__,
        )
        return status_from_exception(
            provider=chosen,
            exc=exc,
            configured=False,
        ).model_dump()

    # Defence-in-depth scrub before serialising — providers should already
    # have scrubbed via build_status(), but a belt-and-braces pass keeps
    # any future direct-message override safe.
    payload = status.model_dump()
    if isinstance(payload.get("message"), str):
        payload["message"] = scrub_secrets(payload["message"])
    return payload


@router.delete("/api-key", response_model=ApiKeySaveResponse)
async def remove_api_key(provider: str | None = None):
    """Remove AI provider API key(s) from the user config file (~/.axion.env).

    If ``provider`` is specified, removes only that provider's key.
    If omitted, removes all provider keys.
    """
    targets = [provider] if provider else list(_PROVIDER_ENV_VARS.keys())

    try:
        for prov in targets:
            env_var = _PROVIDER_ENV_VARS.get(prov)
            if not env_var:
                continue
            _write_env_key(env_var, "")
            # Comment out the empty key line
            lines = _read_env_lines()
            new_lines = []
            for line in lines:
                if line.strip() == f"{env_var}=":
                    new_lines.append(f"# {env_var}=\n")
                else:
                    new_lines.append(line)
            _ENV_FILE.write_text("".join(new_lines), encoding="utf-8")
            logger.info("%s API key removed from %s", prov.title(), _ENV_FILE)

        removed = ", ".join(t.title() for t in targets)
        return ApiKeySaveResponse(
            status="saved",
            message=f"{removed} key(s) removed. Restart Axion to apply.",
            restart_required=True,
        )
    except Exception as e:
        logger.error("Failed to remove API key(s): %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to update .env file: {e}")
