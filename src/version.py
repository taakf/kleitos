"""Phase 17 — single source of truth for Axion release identity.

Before Phase 17 the version string ``"1.0.0"`` was hard-coded
independently in ``src/__init__.py``, ``src/config.py`` and
``src/api/routes/health.py``.  This module consolidates it so the
release identity is defined exactly once and every other site imports
from here.

Design rules
------------
* **Pure + import-light.**  This module imports nothing from the rest
  of ``src`` at module load, so it is safe to import from
  ``src/__init__.py`` and ``src/config.py`` without a circular import.
* **Honest, non-commercial channel.**  ``RELEASE_CHANNEL = "local"``
  marks this as the downloadable, single-machine, loopback-only build
  — never a hosted / multi-tenant SaaS.
* **Version lineage preserved.**  The repository already declared
  ``__version__ = "1.0.0"`` and ships ``RELEASE_NOTES_V1.md``; Phase 17
  keeps that ``1.0.0`` lineage rather than inventing a conflicting
  number.  The ``-local`` nuance is carried by ``RELEASE_CHANNEL`` so
  the bare version string stays stable for the existing
  ``/api/v1/health`` contract.
* **Schema version is not duplicated.**  :func:`schema_version` reads
  ``CURRENT_SCHEMA_VERSION`` from the migrations module on demand so
  there is still exactly one definition of the schema number.
"""

from __future__ import annotations

#: Customer-facing product name.
APP_NAME: str = "Axion"

#: Release version.  Kept on the ``1.0.0`` lineage the repository
#: already established (see ``RELEASE_NOTES_V1.md``).  Bump deliberately.
APP_VERSION: str = "1.0.0"

#: Release channel.  ``local`` = downloadable, single-user, loopback
#: build.  This is the honest marker that Axion is not a hosted service.
RELEASE_CHANNEL: str = "local"

#: A compact human-readable build tag, e.g. ``Axion 1.0.0 (local)``.
RELEASE_TAG: str = f"{APP_NAME} {APP_VERSION} ({RELEASE_CHANNEL})"


def schema_version() -> int | None:
    """Return the schema version this build supports.

    Reads ``CURRENT_SCHEMA_VERSION`` from the migrations module so the
    schema number is never duplicated here.  Returns ``None`` if the
    migrations module cannot be imported (e.g. a partial install) — the
    caller decides how to surface that.
    """
    try:
        from src.database.migrations import CURRENT_SCHEMA_VERSION
        return int(CURRENT_SCHEMA_VERSION)
    except Exception:  # pragma: no cover — defensive
        return None


def release_identity() -> dict[str, object]:
    """Return the release identity as a plain JSON-safe dict.

    Used by the support bundle, the diagnostics endpoint and the
    release-zip manifest so every release-identity surface reports the
    same fields.
    """
    return {
        "app_name": APP_NAME,
        "app_version": APP_VERSION,
        "release_channel": RELEASE_CHANNEL,
        "release_tag": RELEASE_TAG,
        "schema_version": schema_version(),
    }
