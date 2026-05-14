"""Phase 11 — AI-assisted extraction of revenue geography from reports.

Review-first pipeline:

1.  Operator uploads a financial report (PDF preferred; plain text also
    accepted for testability) via
    ``POST /api/v1/exposures/revenue-geography/extract``.
2.  This module renders PDF pages to images and calls the configured
    LLM vision provider with a strict anti-hallucination prompt that
    forbids inference from headquarters, listing exchange, ISIN, or
    incorporation country.  Plain-text uploads skip rendering and go
    straight to the same prompt via :func:`call_llm_json`.
3.  Every candidate is validated against the Phase 10 share-parser +
    region normaliser, soft per-company sum-100 % warnings are
    attached, source URLs are scrubbed.
4.  Nothing is persisted.  The route returns the typed
    :class:`ExtractionResult` to the UI for review.  The operator can
    edit rows and POST them back to ``/confirm-extraction``, which
    reuses the Phase 10 importer with ``source_type="ai_extracted"``.

Hard guarantees:

* No row is created when the LLM returns an empty list — the result
  status becomes ``no_revenue_geography_found`` and the UI tells the
  operator the report had no explicit regional revenue breakdown.
* No row is created when the LLM returns malformed JSON or raises —
  the result status becomes ``extraction_failed``.
* Uploads are processed entirely in memory; no PDF bytes touch disk.
* Logs record filename + byte size + status — never the document body.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from src.intelligence.revenue_geography.service import (
    AllocationWarning,
    normalize_country,
    normalize_region,
    parse_revenue_share,
    validate_company_allocations,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Prompt — frozen anti-hallucination contract
# ─────────────────────────────────────────────────────────────────────


#: The system prompt used for both vision and text extraction.  This
#: text is asserted by ``tests/unit/test_phase11_revenue_geography_ai.py``;
#: changes here must keep every rule below intact.
EXTRACTION_PROMPT: str = (
    "You are extracting REVENUE BY GEOGRAPHIC REGION from a single "
    "issuer's financial report (annual report, 10-K, interim/half-year "
    "results, or investor deck).\n\n"
    "GROUNDING CONTRACT (strict):\n"
    "- Extract ONLY geographic / regional revenue breakdowns that are "
    "EXPLICITLY present in the document (as a chart, a table, a number "
    "in body text, or an itemised list).\n"
    "- Do NOT infer revenue geography from: headquarters location, "
    "country of incorporation, ISIN prefix, listing exchange, "
    "customer names, employee count, or factory addresses. Any of "
    "these as the sole signal must be ignored.\n"
    "- If a row has a region but no percentage, AND no monetary "
    "amount you can convert, do NOT create a candidate for it.\n"
    "- If percentages are absent but revenue amounts per region are "
    "present along with a clear total or per-region currency, compute "
    "the percentages and put the original amount + currency in "
    "``raw_evidence``.\n"
    "- If only narrative text mentions regions without numbers (e.g. "
    "\"we are active in Europe\"), do NOT create a candidate.\n"
    "- Preserve fiscal year and period exactly as shown.\n"
    "- Return EXACTLY this JSON object and nothing else.\n\n"
    "JSON SCHEMA (return only this):\n"
    "{\n"
    "  \"found_revenue_geography\": true | false,\n"
    "  \"fiscal_year\": <int | null>,\n"
    "  \"period\": \"<FY | H1 | Q1 | …>\" | null,\n"
    "  \"currency\": \"<ISO currency or null>\",\n"
    "  \"company_name\": \"<string | null>\",\n"
    "  \"ticker\": \"<string | null>\",\n"
    "  \"isin\": \"<string | null>\",\n"
    "  \"candidates\": [\n"
    "    {\n"
    "      \"region\": \"<string>\",\n"
    "      \"country\": \"<string | null>\",\n"
    "      \"revenue_share\": <float between 0 and 1>,\n"
    "      \"raw_evidence\": \"<short verbatim quote with the number(s)>\",\n"
    "      \"page_number\": <int | null>,\n"
    "      \"confidence\": <0.0 – 1.0>\n"
    "    }\n"
    "  ]\n"
    "}\n\n"
    "If the document does NOT contain explicit geographic revenue "
    "data, return "
    "``{\"found_revenue_geography\": false, \"candidates\": []}``. "
    "Never invent regions, never invent percentages, never round to "
    "a shape that contradicts the source.\n"
)


# ─────────────────────────────────────────────────────────────────────
# Result shape
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ExtractedCandidate:
    """A single AI-extracted row, pre-validation.

    ``confidence`` and ``page_number`` come straight from the LLM;
    ``evidence_text`` is the verbatim snippet the LLM cited.  The
    operator may edit any of these before confirming.
    """

    region: str
    country: str | None
    revenue_share: float            # 0.0 – 1.0
    fiscal_year: int | None
    period: str | None
    currency: str | None
    ticker: str | None
    isin: str | None
    company_name: str | None
    evidence_text: str | None
    page_number: int | None
    confidence: float | None
    share_note: str | None = None   # set when share parsing softened input


@dataclass
class ExtractionResult:
    """Returned by ``/extract`` — never persisted directly.

    ``status`` is the customer-facing token the UI keys off:

    * ``success``                     — at least one valid candidate
    * ``no_revenue_geography_found``  — LLM ran but report had nothing
    * ``missing_key``                 — no provider key configured
    * ``disabled``                    — provider disabled
    * ``unsupported_file``            — file format the pipeline can't handle
    * ``extraction_failed``           — malformed JSON / provider error
    """

    status: str
    reason: str
    provider: str | None = None
    model: str | None = None
    source_filename: str | None = None
    source_size_bytes: int | None = None
    fiscal_year: int | None = None
    period: str | None = None
    currency: str | None = None
    company_name: str | None = None
    ticker: str | None = None
    isin: str | None = None
    candidates: list[ExtractedCandidate] = field(default_factory=list)
    validation_errors: list[dict[str, Any]] = field(default_factory=list)
    validation_warnings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        # Make dataclass-of-dataclasses JSON-serialisable.
        out["candidates"] = [asdict(c) for c in self.candidates]
        return out


# ─────────────────────────────────────────────────────────────────────
# Provider availability detection
# ─────────────────────────────────────────────────────────────────────


def _llm_availability() -> tuple[bool, str, str]:
    """Return ``(available, status, reason)`` without raising.

    The Phase 6 ``is_llm_available()`` returns a single bool; here we
    surface the *reason* so the UI can give a more useful message.
    The settings layer doesn't currently expose a hard "AI disabled"
    flag — operators turn it off by removing the provider key — so
    "missing_key" is the customer-facing status today.  If a future
    settings field surfaces an explicit toggle, route that through
    here as "disabled" without changing the API contract.
    """
    try:
        from src.llm.client import is_llm_available
    except Exception as exc:  # pragma: no cover — import-time issues
        return False, "extraction_failed", f"LLM layer import failed: {exc!r}"

    if not is_llm_available():
        return False, "missing_key", (
            "No AI provider key configured. Set one in Settings → "
            "AI Configuration or add it to ~/.axion.env, then retry. "
            "Manual CSV import remains the supported path."
        )
    return True, "success", "ok"


# ─────────────────────────────────────────────────────────────────────
# Candidate validation (reuses Phase 10 logic)
# ─────────────────────────────────────────────────────────────────────


def _validate_candidates(
    raw_candidates: Iterable[dict[str, Any]],
    *,
    fiscal_year: int | None,
    period: str | None,
    currency: str | None,
    company_name: str | None,
    ticker: str | None,
    isin: str | None,
) -> tuple[list[ExtractedCandidate], list[dict[str, Any]], list[AllocationWarning]]:
    """Normalise + validate a list of LLM candidate dicts.

    Returns ``(candidates, errors, warnings)``.  Bad rows do not abort
    the batch — the operator sees per-row errors in the review table.
    """
    candidates: list[ExtractedCandidate] = []
    errors: list[dict[str, Any]] = []

    for idx, raw in enumerate(raw_candidates or [], start=1):
        if not isinstance(raw, dict):
            errors.append({
                "row": idx, "field": "_root",
                "message": "Candidate is not a JSON object.",
            })
            continue
        region_raw = (raw.get("region") or "").strip()
        share_raw = raw.get("revenue_share")
        if not region_raw:
            errors.append({
                "row": idx, "field": "region",
                "message": "Region is missing.",
            })
            continue
        if share_raw is None:
            errors.append({
                "row": idx, "field": "revenue_share",
                "message": "revenue_share is missing.",
            })
            continue
        try:
            share, share_note = parse_revenue_share(share_raw)
        except ValueError as exc:
            errors.append({
                "row": idx, "field": "revenue_share",
                "message": str(exc),
            })
            continue

        cand_ticker = (
            (raw.get("ticker") or "").strip().upper() or ticker or None
        )
        cand_isin = (
            (raw.get("isin") or "").strip().upper() or isin or None
        )
        cand_company = raw.get("company_name") or company_name
        cand_fy = raw.get("fiscal_year")
        if cand_fy is None:
            cand_fy = fiscal_year
        else:
            try:
                cand_fy = int(cand_fy)
            except (TypeError, ValueError):
                errors.append({
                    "row": idx, "field": "fiscal_year",
                    "message": f"Unparseable fiscal_year {cand_fy!r}",
                })
                continue
        cand_period = raw.get("period") or period
        cand_currency = raw.get("currency") or currency
        page_number = raw.get("page_number")
        if page_number is not None:
            try:
                page_number = int(page_number)
            except (TypeError, ValueError):
                page_number = None
        confidence = raw.get("confidence")
        if confidence is not None:
            try:
                confidence = float(confidence)
            except (TypeError, ValueError):
                confidence = None

        candidates.append(ExtractedCandidate(
            region=normalize_region(region_raw),
            country=normalize_country(raw.get("country")),
            revenue_share=share,
            fiscal_year=cand_fy,
            period=cand_period,
            currency=cand_currency,
            ticker=cand_ticker,
            isin=cand_isin,
            company_name=cand_company,
            evidence_text=(raw.get("raw_evidence") or raw.get("evidence_text") or None),
            page_number=page_number,
            confidence=confidence,
            share_note=share_note,
        ))

    # Reuse the Phase 10 sum-100 warning helper.
    warning_rows = [
        {
            "ticker": c.ticker, "isin": c.isin,
            "fiscal_year": c.fiscal_year, "period": c.period,
            "region": c.region, "revenue_share": c.revenue_share,
        }
        for c in candidates
    ]
    warnings = validate_company_allocations(warning_rows)
    return candidates, errors, warnings


# ─────────────────────────────────────────────────────────────────────
# Top-level entry points
# ─────────────────────────────────────────────────────────────────────


async def extract_from_text(
    *,
    text: str,
    source_filename: str | None = None,
    ticker_hint: str | None = None,
    isin_hint: str | None = None,
) -> ExtractionResult:
    """Run the extraction prompt against a plain-text payload.

    Used for testability (mockable LLM JSON call) and for operators
    who copy/paste a results-press-release snippet rather than
    uploading a full PDF.
    """
    avail, status, reason = _llm_availability()
    if not avail:
        return ExtractionResult(
            status=status, reason=reason,
            source_filename=source_filename,
            source_size_bytes=len(text.encode("utf-8")) if text else 0,
        )

    if not text or not text.strip():
        return ExtractionResult(
            status="unsupported_file",
            reason="Empty text payload — paste the regional-revenue passage.",
            source_filename=source_filename,
        )

    user_payload = (
        f"{EXTRACTION_PROMPT}\n\n"
        f"DOCUMENT (verbatim, do not infer beyond this text):\n"
        f"---\n{text}\n---\n"
    )

    try:
        from src.llm.client import call_llm_json, LLMUnavailableError
    except Exception as exc:  # pragma: no cover — defensive
        return ExtractionResult(
            status="extraction_failed",
            reason=f"AI layer not importable: {exc!r}",
            source_filename=source_filename,
        )

    try:
        raw = await call_llm_json(user_payload)
    except LLMUnavailableError as exc:
        return ExtractionResult(
            status="missing_key", reason=str(exc),
            source_filename=source_filename,
        )
    except Exception as exc:
        logger.warning("AI extraction call failed: %r", exc)
        return ExtractionResult(
            status="extraction_failed",
            reason=(
                "AI provider returned an error or unparseable response. "
                "Try again or use the manual CSV import."
            ),
            source_filename=source_filename,
        )

    return _build_result(
        raw, source_filename=source_filename,
        source_size_bytes=len(text.encode("utf-8")),
        ticker_hint=ticker_hint, isin_hint=isin_hint,
    )


async def extract_from_pdf_bytes(
    *,
    pdf_bytes: bytes,
    source_filename: str | None = None,
    ticker_hint: str | None = None,
    isin_hint: str | None = None,
    max_pages: int = 5,
) -> ExtractionResult:
    """Render the PDF pages to images and pass them to the vision LLM.

    Only the first ``max_pages`` pages are sent so a 200-page annual
    report doesn't run up provider costs.  The operator can re-upload
    a trimmed PDF if a later page contains the regional breakdown.

    PDF bytes are never written to disk — pdfplumber reads from a
    :class:`io.BytesIO` and the rendered page images stay in memory.
    """
    avail, status, reason = _llm_availability()
    if not avail:
        return ExtractionResult(
            status=status, reason=reason,
            source_filename=source_filename,
            source_size_bytes=len(pdf_bytes or b""),
        )

    if not pdf_bytes:
        return ExtractionResult(
            status="unsupported_file",
            reason="Empty PDF payload.",
            source_filename=source_filename,
        )

    try:
        import io
        import pdfplumber                         # noqa: F401 — runtime gate
    except ImportError:
        return ExtractionResult(
            status="unsupported_file",
            reason=(
                "PDF rendering is unavailable in this build "
                "(pdfplumber not installed). Paste the regional-revenue "
                "passage as text instead."
            ),
            source_filename=source_filename,
        )

    try:
        from src.llm.client import call_llm_vision_json, LLMUnavailableError
    except Exception as exc:  # pragma: no cover — defensive
        return ExtractionResult(
            status="extraction_failed",
            reason=f"AI layer not importable: {exc!r}",
            source_filename=source_filename,
        )

    # Render pages → call vision → aggregate candidates across pages.
    combined: dict[str, Any] = {
        "found_revenue_geography": False,
        "candidates": [],
    }
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages_processed = min(len(pdf.pages), max_pages)
            for page_idx in range(pages_processed):
                page = pdf.pages[page_idx]
                try:
                    page_image = page.to_image(resolution=200)
                    img_buffer = io.BytesIO()
                    page_image.original.save(img_buffer, format="PNG")
                    img_bytes = img_buffer.getvalue()
                except Exception as exc:  # pragma: no cover — pdf-specific
                    logger.warning(
                        "PDF page %d render failed: %r", page_idx + 1, exc,
                    )
                    continue

                logger.info(
                    "AI revenue-geography extraction: page %d/%d (%d bytes) — %s",
                    page_idx + 1, pages_processed, len(img_bytes),
                    source_filename or "(unnamed)",
                )

                try:
                    page_payload = await call_llm_vision_json(
                        EXTRACTION_PROMPT, img_bytes, "image/png",
                    )
                except LLMUnavailableError as exc:
                    return ExtractionResult(
                        status="missing_key", reason=str(exc),
                        source_filename=source_filename,
                        source_size_bytes=len(pdf_bytes),
                    )
                except Exception as exc:
                    logger.warning(
                        "Vision call failed on page %d: %r", page_idx + 1, exc,
                    )
                    continue

                if isinstance(page_payload, dict):
                    if page_payload.get("found_revenue_geography"):
                        combined["found_revenue_geography"] = True
                    cands = page_payload.get("candidates") or []
                    if isinstance(cands, list):
                        # Stamp page_number when the LLM omitted it.
                        for c in cands:
                            if isinstance(c, dict) and c.get("page_number") is None:
                                c["page_number"] = page_idx + 1
                            combined["candidates"].append(c)
                    # Promote top-level identifiers from the first page
                    # that supplies them.
                    for k in ("fiscal_year", "period", "currency",
                              "company_name", "ticker", "isin"):
                        if not combined.get(k) and page_payload.get(k):
                            combined[k] = page_payload[k]
    except Exception as exc:
        logger.warning("PDF read failed: %r", exc)
        return ExtractionResult(
            status="unsupported_file",
            reason=(
                "Could not open the uploaded PDF. Verify it is not "
                "corrupt or password-protected."
            ),
            source_filename=source_filename,
            source_size_bytes=len(pdf_bytes),
        )

    return _build_result(
        combined, source_filename=source_filename,
        source_size_bytes=len(pdf_bytes),
        ticker_hint=ticker_hint, isin_hint=isin_hint,
    )


# ─────────────────────────────────────────────────────────────────────
# Shared result builder
# ─────────────────────────────────────────────────────────────────────


def _build_result(
    raw: Any,
    *,
    source_filename: str | None,
    source_size_bytes: int,
    ticker_hint: str | None,
    isin_hint: str | None,
) -> ExtractionResult:
    """Validate the LLM's JSON shape and assemble an ExtractionResult."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return ExtractionResult(
                status="extraction_failed",
                reason="AI returned non-JSON output.",
                source_filename=source_filename,
                source_size_bytes=source_size_bytes,
            )
    if not isinstance(raw, dict):
        return ExtractionResult(
            status="extraction_failed",
            reason="AI returned an unexpected shape (expected JSON object).",
            source_filename=source_filename,
            source_size_bytes=source_size_bytes,
        )

    found = bool(raw.get("found_revenue_geography"))
    raw_candidates = raw.get("candidates") or []
    if not isinstance(raw_candidates, list):
        return ExtractionResult(
            status="extraction_failed",
            reason="AI returned candidates in an unexpected shape.",
            source_filename=source_filename,
            source_size_bytes=source_size_bytes,
        )

    ticker_top = (raw.get("ticker") or ticker_hint or None)
    isin_top = (raw.get("isin") or isin_hint or None)
    candidates, errors, warnings = _validate_candidates(
        raw_candidates,
        fiscal_year=_to_int(raw.get("fiscal_year")),
        period=raw.get("period"),
        currency=raw.get("currency"),
        company_name=raw.get("company_name"),
        ticker=ticker_top,
        isin=isin_top,
    )

    if not found and not candidates:
        return ExtractionResult(
            status="no_revenue_geography_found",
            reason=(
                "The report did not contain an explicit geographic "
                "revenue breakdown. Try a more detailed annual report "
                "or paste the relevant section as text."
            ),
            source_filename=source_filename,
            source_size_bytes=source_size_bytes,
            fiscal_year=_to_int(raw.get("fiscal_year")),
            period=raw.get("period"),
            currency=raw.get("currency"),
            company_name=raw.get("company_name"),
            ticker=ticker_top, isin=isin_top,
            validation_errors=errors,
        )

    if not candidates:
        return ExtractionResult(
            status="extraction_failed",
            reason="Every candidate row failed validation.",
            source_filename=source_filename,
            source_size_bytes=source_size_bytes,
            fiscal_year=_to_int(raw.get("fiscal_year")),
            period=raw.get("period"),
            currency=raw.get("currency"),
            company_name=raw.get("company_name"),
            ticker=ticker_top, isin=isin_top,
            validation_errors=errors,
        )

    return ExtractionResult(
        status="success",
        reason="OK",
        source_filename=source_filename,
        source_size_bytes=source_size_bytes,
        fiscal_year=_to_int(raw.get("fiscal_year")),
        period=raw.get("period"),
        currency=raw.get("currency"),
        company_name=raw.get("company_name"),
        ticker=ticker_top, isin=isin_top,
        candidates=candidates,
        validation_errors=errors,
        validation_warnings=[
            {"key": w.key, "kind": w.kind, "message": w.message,
             "sum_pct": w.sum_pct}
            for w in warnings
        ],
    )


def _to_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


__all__ = [
    "EXTRACTION_PROMPT",
    "ExtractedCandidate",
    "ExtractionResult",
    "_llm_availability",
    "extract_from_pdf_bytes",
    "extract_from_text",
]
