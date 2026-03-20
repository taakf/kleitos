"""Portfolio document extraction — CSV, PDF, and image parsing.

Extracts tabular portfolio data from uploaded files and returns structured
rows for human review before import. Conservative: never auto-imports.

Supports:
- CSV: direct column-mapped parsing
- PDF: table extraction via pdfplumber, with AI vision fallback for scanned PDFs
- Images (PNG/JPG): AI vision extraction via configured LLM provider
"""

import csv
import io
import logging
import re
from typing import Any

logger = logging.getLogger("axion.extract")

# Column aliases → canonical field names (same as PortfolioLedger.parse_csv)
COLUMN_MAP = {
    "ticker": "ticker", "symbol": "ticker", "stock": "ticker", "code": "ticker",
    "name": "name", "security": "name", "holding": "name", "description": "name",
    "quantity": "quantity", "shares": "quantity", "qty": "quantity", "units": "quantity",
    "price": "current_price", "current_price": "current_price", "last_price": "current_price",
    "mkt_price": "current_price", "market_price": "current_price",
    "cost": "avg_cost_basis", "avg_cost": "avg_cost_basis", "cost_basis": "avg_cost_basis",
    "average_cost": "avg_cost_basis", "avg_cost_basis": "avg_cost_basis",
    "currency": "currency", "ccy": "currency",
    "isin": "isin", "venue": "venue", "exchange": "venue", "market": "venue",
    "weight": "weight_pct", "weight_pct": "weight_pct", "pct": "weight_pct",
    "value": "market_value", "market_value": "market_value", "mkt_value": "market_value",
}

NUMERIC_FIELDS = {"quantity", "current_price", "avg_cost_basis", "market_value", "weight_pct"}

# Vision extraction prompt — instructs the AI to extract tabular portfolio data
_VISION_PROMPT = """\
You are an expert financial document reader. Extract all portfolio holdings \
from this image and return them as a JSON object.

Return ONLY valid JSON with this exact structure:
{
  "holdings": [
    {
      "ticker": "AAPL",
      "name": "Apple Inc.",
      "quantity": 100,
      "current_price": 175.50,
      "avg_cost_basis": 150.00,
      "market_value": 17550.00,
      "currency": "USD",
      "weight_pct": 5.2
    }
  ]
}

Rules:
- Extract every holding/position you can see in the image.
- Use the exact field names shown above.
- For numeric fields, return numbers (not strings). Use null if not visible.
- ticker is required — skip rows where you cannot determine a ticker symbol.
- If currency is not shown, use "USD" as default.
- If a field is not visible in the image, set it to null.
- Return ONLY the JSON object, no explanation or commentary.
"""


def _normalize_col(col: str) -> str:
    """Normalize a column header for matching."""
    return re.sub(r"[^a-z0-9_]", "_", col.strip().lower()).strip("_")


def _parse_number(val: str | None) -> float | None:
    """Parse a numeric string, stripping currency symbols and commas."""
    if not val or not val.strip():
        return None
    cleaned = re.sub(r"[^\d.\-]", "", val.strip())
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _row_to_holding(raw: dict[str, str], col_mapping: dict[str, str]) -> dict[str, Any] | None:
    """Convert a raw row dict to a canonical holding dict using column mapping."""
    holding: dict[str, Any] = {}
    for raw_col, value in raw.items():
        target = col_mapping.get(_normalize_col(raw_col))
        if not target:
            continue
        if target in NUMERIC_FIELDS:
            holding[target] = _parse_number(value)
        else:
            holding[target] = value.strip() if value else None

    if not holding.get("ticker"):
        return None
    return holding


def _vision_result_to_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a vision extraction JSON result to canonical holding dicts.

    Accepts the structured JSON returned by the vision prompt and
    normalizes it to the same format as CSV/PDF extraction.
    """
    holdings = result.get("holdings", [])
    if not isinstance(holdings, list):
        logger.warning("Vision result 'holdings' is not a list: %s", type(holdings))
        return []

    rows: list[dict[str, Any]] = []
    for h in holdings:
        if not isinstance(h, dict):
            continue
        ticker = h.get("ticker")
        if not ticker or not str(ticker).strip():
            continue

        row: dict[str, Any] = {"ticker": str(ticker).strip().upper()}

        # Map known fields
        if h.get("name"):
            row["name"] = str(h["name"]).strip()
        for num_field in ("quantity", "current_price", "avg_cost_basis", "market_value", "weight_pct"):
            val = h.get(num_field)
            if val is not None:
                try:
                    row[num_field] = float(val)
                except (ValueError, TypeError):
                    pass
        if h.get("currency"):
            row["currency"] = str(h["currency"]).strip().upper()

        rows.append(row)

    return rows


# ---------------------------------------------------------------------------
# CSV extraction
# ---------------------------------------------------------------------------
def extract_csv(content: str) -> list[dict[str, Any]]:
    """Extract holdings rows from CSV text. Returns list of canonical dicts."""
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        return []

    # Build column mapping for this file's headers
    col_mapping = {}
    for raw_col in reader.fieldnames:
        normalized = _normalize_col(raw_col)
        target = COLUMN_MAP.get(normalized)
        if target:
            col_mapping[normalized] = target

    rows = []
    for raw_row in reader:
        holding = _row_to_holding(raw_row, col_mapping)
        if holding:
            rows.append(holding)

    return rows


# ---------------------------------------------------------------------------
# PDF extraction (pdfplumber + vision fallback)
# ---------------------------------------------------------------------------
def extract_pdf(file_bytes: bytes) -> list[dict[str, Any]]:
    """Extract holdings from a PDF file using pdfplumber table detection.

    If pdfplumber finds no tables (likely a scanned PDF), returns an empty
    list. The caller can then try ``extract_pdf_vision()`` as a fallback.
    """
    try:
        import pdfplumber
    except ImportError:
        logger.error("pdfplumber not installed — cannot extract PDF")
        return []

    rows: list[dict[str, Any]] = []

    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    if not table or len(table) < 2:
                        continue

                    # First row is assumed to be headers
                    raw_headers = [str(h or "").strip() for h in table[0]]
                    col_mapping = {}
                    for i, h in enumerate(raw_headers):
                        normalized = _normalize_col(h)
                        target = COLUMN_MAP.get(normalized)
                        if target:
                            col_mapping[normalized] = target

                    if "ticker" not in col_mapping.values():
                        # No ticker column found — skip this table
                        continue

                    for data_row in table[1:]:
                        raw_row = {}
                        for i, val in enumerate(data_row):
                            if i < len(raw_headers):
                                raw_row[raw_headers[i]] = str(val or "").strip()

                        holding = _row_to_holding(raw_row, col_mapping)
                        if holding:
                            rows.append(holding)
    except Exception as e:
        logger.error("PDF extraction failed: %s", e)

    return rows


async def extract_pdf_vision(file_bytes: bytes, filename: str) -> list[dict[str, Any]]:
    """Extract holdings from a scanned PDF using AI vision.

    Converts the first few pages to images, then sends them to the
    configured AI vision provider for extraction. Returns an empty list
    if no AI provider is available or vision extraction fails.
    """
    from src.llm.client import is_llm_available, call_llm_vision_json, LLMUnavailableError

    if not is_llm_available():
        logger.info("No AI provider available for PDF vision extraction")
        return []

    # Try to render PDF pages to images using pdfplumber
    try:
        import pdfplumber
    except ImportError:
        logger.error("pdfplumber not installed — cannot render PDF pages")
        return []

    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            # Process up to first 3 pages (portfolio tables rarely span more)
            max_pages = min(len(pdf.pages), 3)
            all_rows: list[dict[str, Any]] = []

            for page_idx in range(max_pages):
                page = pdf.pages[page_idx]
                try:
                    # Render page to image
                    page_image = page.to_image(resolution=200)

                    # Save to PNG bytes
                    img_buffer = io.BytesIO()
                    page_image.original.save(img_buffer, format="PNG")
                    img_bytes = img_buffer.getvalue()

                    logger.info(
                        "Sending PDF page %d/%d to vision (%d bytes) — %s",
                        page_idx + 1, max_pages, len(img_bytes), filename,
                    )

                    result = await call_llm_vision_json(
                        _VISION_PROMPT,
                        img_bytes,
                        "image/png",
                        temperature=0.05,
                        max_tokens=4096,
                    )
                    rows = _vision_result_to_rows(result)
                    all_rows.extend(rows)
                    logger.info(
                        "Vision extracted %d holdings from page %d of %s",
                        len(rows), page_idx + 1, filename,
                    )
                except NotImplementedError:
                    logger.warning("AI provider does not support vision — cannot extract scanned PDF")
                    return []
                except LLMUnavailableError:
                    logger.warning("AI provider unavailable for PDF vision extraction")
                    return []
                except Exception as e:
                    logger.warning("Vision extraction failed for page %d: %s", page_idx + 1, e)
                    continue

            # Deduplicate by ticker (keep first occurrence)
            seen: set[str] = set()
            deduped: list[dict[str, Any]] = []
            for row in all_rows:
                t = row.get("ticker", "")
                if t not in seen:
                    seen.add(t)
                    deduped.append(row)
            return deduped

    except Exception as e:
        logger.error("PDF vision extraction failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# Image extraction (AI vision)
# ---------------------------------------------------------------------------
async def extract_image(file_bytes: bytes, filename: str) -> list[dict[str, Any]]:
    """Extract holdings from an image file using AI vision.

    Sends the image to the configured LLM provider with a structured
    extraction prompt and parses the JSON response. Returns an empty
    list if no AI provider is available or extraction fails.
    """
    from src.llm.client import is_llm_available, call_llm_vision_json, LLMUnavailableError

    if not is_llm_available():
        logger.info(
            "Image extraction requested for %s (%d bytes) — no AI provider available",
            filename, len(file_bytes),
        )
        return []

    # Determine media type from filename
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
    media_types = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
        "gif": "image/gif",
    }
    media_type = media_types.get(ext, "image/png")

    logger.info(
        "Sending image to AI vision for extraction: %s (%d bytes, %s)",
        filename, len(file_bytes), media_type,
    )

    try:
        result = await call_llm_vision_json(
            _VISION_PROMPT,
            file_bytes,
            media_type,
            temperature=0.05,
            max_tokens=4096,
        )
        rows = _vision_result_to_rows(result)
        logger.info(
            "Vision extracted %d holdings from %s",
            len(rows), filename,
        )
        return rows

    except NotImplementedError:
        logger.warning(
            "AI provider does not support vision — cannot extract image %s",
            filename,
        )
        return []
    except LLMUnavailableError:
        logger.info(
            "AI provider unavailable for image extraction of %s",
            filename,
        )
        return []
    except Exception as e:
        logger.error("Image vision extraction failed for %s: %s", filename, e)
        return []
