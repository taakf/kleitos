"""Deterministic event → macro factor classifier (Phase 9A).

Pure regex / keyword based.  No AI, no network, no heuristic drift
between runs.  Given the same event text, the classifier always
returns the same list of ``FactorClassification`` results.

Design principles
-----------------
* **Conservative first**.  Weak, ambiguous, or single-weak-word
  matches produce NO classification.  Noise in the sync report
  called out that the existing geography/sector matching had to be
  AND-gated to stay usable; the factor classifier follows the same
  discipline.
* **Multi-factor allowed**.  A pipeline attack can legitimately fire
  ``oil_energy`` and ``geopolitical_risk`` simultaneously.
* **Directionality is cheap, magnitude is earned**.  Direction cues
  are a closed dictionary of verbs; magnitude is only promoted to
  ``major`` or ``extreme`` when the text carries an explicit numeric
  anchor (e.g. ``50 bps``, ``12%``) or an explicit amplifier
  (``surge``, ``plunge``, ``record``).
* **Company-name false positives**.  ``Apple`` (the fruit or the
  corporate) must never produce a macro tag by itself.  The rules
  only fire on factor-specific vocabulary, so "Apple orchard
  destroyed by frost" never reaches the factor stage.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------


@dataclass
class FactorClassification:
    """Single event-to-factor classification result.

    Attributes
    ----------
    factor:
        Stable factor key from ``taxonomy.FACTORS``.
    direction:
        ``"up"`` | ``"down"`` | ``"unknown"``.  Semantics are factor-
        specific but always "up = the direction described by the
        factor label".
    magnitude:
        ``"minor"`` | ``"moderate"`` | ``"major"`` | ``"extreme"``
        | ``"unknown"``.
    confidence:
        Float in [0.05, 0.95].  Produced by the conservative
        weighting formula in ``_score_factor``.
    rationale:
        List of short strings describing the patterns that fired
        (``"matched: fed raises"``, ``"parsed: 50 bps"``, etc.).
        Persisted verbatim to ``macro_factor_events.rationale`` so
        downstream consumers can explain the classification.
    """

    factor: str
    direction: str = "unknown"
    magnitude: str = "unknown"
    confidence: float = 0.0
    rationale: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Direction / magnitude vocabulary
# ---------------------------------------------------------------------------

# Closed dictionary — small on purpose.  Add with care.
_UP_CUES: tuple[str, ...] = (
    "hike", "hikes", "hiked", "raise", "raises", "raised",
    "increase", "increases", "increased",
    "tighten", "tightens", "tightened", "tightening",
    "widen", "widens", "widened", "widening",
    "surge", "surges", "surged", "surging",
    "jump", "jumps", "jumped", "jumping",
    "spike", "spikes", "spiked", "spiking",
    "escalate", "escalates", "escalated", "escalation", "escalating",
    "soar", "soars", "soared", "soaring",
    "accelerate", "accelerates", "accelerated", "accelerating",
    "rally", "rallies", "rallied",
    "rise", "rises", "rose", "rising",
    "climb", "climbs", "climbed", "climbing",
    "strengthen", "strengthens", "strengthened", "strengthening",
    "tariff", "tariffs", "sanction", "sanctions", "embargo",
)

_DOWN_CUES: tuple[str, ...] = (
    "cut", "cuts", "cuts in",
    "lower", "lowers", "lowered", "lowering",
    "decrease", "decreases", "decreased", "decreasing",
    "ease", "eases", "eased", "easing",
    "narrow", "narrows", "narrowed", "narrowing",
    "fall", "falls", "fell", "fallen", "falling",
    "drop", "drops", "dropped", "dropping",
    "plunge", "plunges", "plunged", "plunging",
    "slump", "slumps", "slumped", "slumping",
    "slide", "slides", "slid", "sliding",
    "weaken", "weakens", "weakened", "weakening",
    "de-escalate", "de-escalates", "de-escalated", "de-escalation",
    "ceasefire", "truce",
)

_EXTREME_AMPLIFIERS: tuple[str, ...] = (
    "record", "historic", "unprecedented", "crisis", "crash",
    "collapse", "meltdown", "emergency",
)

# Numeric anchor patterns
# - 50 bp / 50bps / 50 basis points    -> rate move
# - 12.3%, 12 percent                  -> percentage move
# Note: the trailing anchor for % uses a lookahead rather than \b
# because '%' is not a word character, so \b after '%' fails when
# followed by whitespace (non-word → non-word).
_RE_BPS = re.compile(
    r"\b(\d{1,4}(?:\.\d+)?)\s*(?:bp|bps|basis\s+points?)\b",
    re.IGNORECASE,
)
_RE_PCT = re.compile(
    r"\b(\d{1,3}(?:\.\d+)?)\s*(?:%|percent|per\s+cent)(?=\b|\s|[,.;:!?]|$)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Factor pattern library
# ---------------------------------------------------------------------------
# Each factor has two tiers:
#   * core_patterns    — unambiguous, high-precision phrases.  A single
#                         core match is enough to consider the factor
#                         (but still subject to the global confidence
#                         floor).
#   * support_patterns — weaker supporting hits that *only* matter when
#                         accompanied by either a core match or another
#                         support match.  They never classify alone.
#
# All patterns are compiled with IGNORECASE and matched with word
# boundaries where appropriate.  Patterns are raw strings so that
# authors can use ``\b`` and character classes directly.

_FACTOR_PATTERNS: dict[str, dict[str, tuple[str, ...]]] = {
    "interest_rate": {
        "core": (
            r"\bfederal reserve\b",
            r"\bfomc\b",
            r"\bfed funds\b",
            r"\binterest rate(?:s)?\b",
            r"\brate (?:hike|cut|decision|move|increase|decrease)\b",
            r"\b(?:ecb|bank of england|boe|boj|bank of japan|pboc)\b",
            r"\bpolicy rate\b",
            r"\bmonetary policy\b",
            r"\bquantitative (?:easing|tightening)\b",
            r"\b(?:qe|qt)\b",
            r"\btreasury yield(?:s)?\b",
            r"\byield curve\b",
            r"\b(?:10|2|30)[-\s]?year (?:treasury|yield|note|bond)\b",
            r"\bbond yield(?:s)?\b",
        ),
        "support": (
            r"\b(?:dovish|hawkish)\b",
            r"\brate path\b",
            r"\bpowell\b",
            r"\blagarde\b",
            r"\bbasis points?\b",
        ),
    },
    "inflation": {
        "core": (
            r"\bconsumer price index\b",
            r"\bcpi\b",
            r"\bcore cpi\b",
            r"\bpce\b",
            r"\bcore pce\b",
            r"\bproducer price index\b",
            r"\bppi\b",
            r"\binflation\b",
            r"\bcore inflation\b",
            r"\bprice pressures?\b",
            r"\bwage (?:growth|inflation|pressures?)\b",
            r"\bdisinflation\b",
            r"\bdeflation\b",
        ),
        "support": (
            r"\byear[-\s]?on[-\s]?year\b",
            r"\bmonth[-\s]?on[-\s]?month\b",
            r"\bheadline\b",
            r"\bprice growth\b",
        ),
    },
    "credit_conditions": {
        "core": (
            r"\bcredit spread(?:s)?\b",
            r"\bcredit stress\b",
            r"\bfunding stress\b",
            r"\bliquidity stress\b",
            r"\bhigh[-\s]?yield spread(?:s)?\b",
            r"\binvestment[-\s]?grade spread(?:s)?\b",
            r"\bcredit crunch\b",
            r"\bcredit crisis\b",
            r"\bdowngrad(?:e|ed|ing|es)\b",
            r"\brating (?:cut|downgrade|action)\b",
            r"\bdefault(?:s|ed)?\b",
            r"\bbankruptc(?:y|ies)\b",
            r"\binsolvenc(?:y|ies)\b",
        ),
        "support": (
            r"\b(?:moody'?s|s&p|fitch)\b",
            r"\bcredit default swap(?:s)?\b",
            r"\bcds\b",
        ),
    },
    "oil_energy": {
        "core": (
            r"\bopec\+?\b",
            r"\bbrent\b",
            r"\bwti\b",
            r"\bcrude oil\b",
            r"\boil price(?:s)?\b",
            r"\bnatural gas\b",
            r"\bgas prices?\b",
            r"\bpipeline\b",
            r"\brefiner(?:y|ies)\b",
            r"\bshipping lane(?:s)?\b",
            r"\bstrait of hormuz\b",
            r"\bsuez\b",
            r"\bliquefied natural gas\b",
            r"\blng\b",
        ),
        "support": (
            r"\boutput cut(?:s)?\b",
            r"\bproduction cut(?:s)?\b",
            r"\bbarrel(?:s)?\b",
            r"\bper barrel\b",
            r"\bshutdown\b",
            r"\bdisruption\b",
            r"\battack\b",
        ),
    },
    "usd_fx": {
        "core": (
            r"\bdollar index\b",
            r"\bdxy\b",
            r"\bu\.s\.\s*dollar\b",
            r"\bus dollar\b",
            r"\bgreenback\b",
            r"\busd[/-](?:jpy|eur|gbp|cnh|cny|chf)\b",
            r"\b(?:euro|yen|sterling|yuan|renminbi)\s+(?:against|versus|vs\.?)\s+(?:the\s+)?dollar\b",
        ),
        "support": (
            r"\bfx market(?:s)?\b",
            r"\bcurrency market(?:s)?\b",
            r"\bdollar strength\b",
            r"\bdollar weakness\b",
        ),
    },
    "trade_policy": {
        "core": (
            r"\btariff(?:s)?\b",
            r"\bduties\b",
            r"\bexport control(?:s)?\b",
            r"\bimport ban(?:s)?\b",
            r"\bexport ban(?:s)?\b",
            r"\bsanction(?:s|ed|ing)?\b",
            r"\bembargo(?:es|ed)?\b",
            r"\btrade war\b",
            r"\btrade deal\b",
            r"\btrade agreement\b",
            r"\bwto\b",
        ),
        "support": (
            r"\bsection 301\b",
            r"\bsection 232\b",
            r"\bentity list\b",
            r"\bcfius\b",
        ),
    },
    "geopolitical_risk": {
        "core": (
            r"\bwar\b",
            r"\binvasion\b",
            r"\binvade(?:s|d)?\b",
            r"\bmissile(?:s)?\b",
            r"\bairstrike(?:s)?\b",
            r"\bdrone strike(?:s)?\b",
            r"\bmilitary strike(?:s)?\b",
            r"\battack(?:s|ed)?\s+on\b",
            r"\bmobilization\b",
            r"\bmobilisation\b",
            r"\bconflict\b",
            r"\bescalat(?:e|es|ed|ion|ing)\b",
            r"\bceasefire\b",
            r"\bcoup\b",
            r"\binsurgenc(?:y|ies)\b",
        ),
        "support": (
            r"\bnato\b",
            r"\bpentagon\b",
            r"\bkremlin\b",
            r"\bgaza\b",
            r"\bukraine\b",
            r"\btaiwan strait\b",
        ),
    },
    "regulation_policy": {
        "core": (
            r"\bantitrust\b",
            r"\banti[-\s]?monopoly\b",
            r"\bregulator(?:y|s)?\b",
            r"\bregulation\b",
            r"\benforcement action(?:s)?\b",
            r"\brulemaking\b",
            r"\bcompliance burden\b",
            r"\bcompliance cost(?:s)?\b",
            r"\blandmark (?:ruling|regulation|law)\b",
            r"\bpolicy shock\b",
            r"\bexecutive order\b",
            r"\bfda (?:approval|rejection|warning)\b",
            r"\bftc\b",
            r"\bdoj\b",
            r"\bsec\s+(?:charges|enforcement|rule|rules|probe)\b",
        ),
        "support": (
            r"\binvestigation\b",
            r"\bprobe\b",
            r"\blawsuit\b",
            r"\bsettlement\b",
        ),
    },
    "consumer_demand": {
        "core": (
            r"\bretail sales\b",
            r"\bconsumer confidence\b",
            r"\bconsumer sentiment\b",
            r"\bdiscretionary spending\b",
            r"\bdemand slowdown\b",
            r"\bdemand surge\b",
            r"\bnonfarm payrolls?\b",
            r"\bjobs? report\b",
            r"\bunemployment rate\b",
            r"\bjobless claims?\b",
            r"\bemployment report\b",
            r"\bwage growth\b",
        ),
        "support": (
            r"\bconsumer\b",
            r"\bhousehold spending\b",
            r"\bfoot traffic\b",
            r"\bsame[-\s]?store sales\b",
        ),
    },
    "technology_cycle": {
        "core": (
            r"\bchip cycle\b",
            r"\bsemiconductor cycle\b",
            r"\bai cycle\b",
            r"\bai adoption\b",
            r"\bplatform shift\b",
            r"\bexport control(?:s)?\s+(?:on|against)\s+(?:chip|semiconductor|ai)",
            r"\bchip (?:ban|restriction|export)\b",
            r"\bai restriction(?:s)?\b",
            r"\bgpu (?:shortage|supply|demand)\b",
            r"\bhyperscaler(?:s)?\b",
            r"\bcapex cycle\b",
        ),
        "support": (
            r"\bnvidia\b",
            r"\btsmc\b",
            r"\basml\b",
            r"\bdata center build[-\s]?out\b",
        ),
    },
}


# ---------------------------------------------------------------------------
# Pre-compilation
# ---------------------------------------------------------------------------

def _compile(patterns: tuple[str, ...]) -> list[re.Pattern[str]]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


_COMPILED: dict[str, dict[str, list[re.Pattern[str]]]] = {
    factor: {
        "core": _compile(tiers["core"]),
        "support": _compile(tiers["support"]),
    }
    for factor, tiers in _FACTOR_PATTERNS.items()
}


# ---------------------------------------------------------------------------
# The classifier
# ---------------------------------------------------------------------------


class FactorClassifier:
    """Conservative deterministic event → macro factor classifier.

    The classifier is stateless; the single public method
    :meth:`classify` returns a list of :class:`FactorClassification`
    results, one per factor that survives the confidence floor.

    Phase 9C: the confidence floor now reads from
    ``ConfidencePolicy.classifier_min_confidence`` by default so
    evaluation runs can swap policies without touching this class.
    The legacy class attribute ``MIN_CONFIDENCE`` is preserved as a
    backward-compatible alias; nothing else in the repo should set it.
    """

    #: Backward-compatible alias — DO NOT set on the class.  Runtime
    #: reads are delegated to ``ConfidencePolicy`` via ``classify``.
    MIN_CONFIDENCE = 0.35

    def __init__(self, policy=None) -> None:
        # Lazy import to avoid a circular with src.intelligence.policy
        # when the package is loaded from the evaluation harness.
        from src.intelligence.policy import get_active_policy

        self._policy = policy if policy is not None else get_active_policy()

    def classify(
        self,
        title: str,
        summary: str = "",
        content: str = "",
    ) -> list[FactorClassification]:
        """Return all factor classifications that survive the floor.

        Parameters
        ----------
        title:
            Event headline (weighted most heavily).
        summary:
            Optional summary / first paragraph.
        content:
            Optional full body text.
        """
        title = (title or "").strip()
        summary = (summary or "").strip()
        content = (content or "").strip()

        if not (title or summary or content):
            return []

        # Concatenate but preserve the title preference: repeat the
        # title so core matches inside the headline count twice.
        text = " ".join([title, title, summary, content])

        # Policy-driven minimum confidence — still honors the
        # legacy class attribute if someone has monkeypatched it
        # (defensive backward compat for external tests).
        policy_min = getattr(self._policy, "classifier_min_confidence", self.MIN_CONFIDENCE)
        min_confidence = max(policy_min, self.MIN_CONFIDENCE) if self.MIN_CONFIDENCE != 0.35 else policy_min

        results: list[FactorClassification] = []
        for factor_key in _COMPILED:
            cls = self._score_factor(factor_key, text, title)
            if cls is not None and cls.confidence >= min_confidence:
                results.append(cls)

        return results

    # ------------------------------------------------------------------
    # Per-factor scoring
    # ------------------------------------------------------------------

    def _score_factor(
        self,
        factor_key: str,
        text: str,
        title: str,
    ) -> FactorClassification | None:
        """Score a single factor against the combined text.

        Returns None if no patterns matched (no classification to emit).
        """
        compiled = _COMPILED[factor_key]
        rationale: list[str] = []

        core_hits: list[str] = []
        for pat in compiled["core"]:
            m = pat.search(text)
            if m:
                core_hits.append(m.group(0))
                rationale.append(f"matched[core]: {m.group(0).strip()}")

        support_hits: list[str] = []
        for pat in compiled["support"]:
            m = pat.search(text)
            if m:
                support_hits.append(m.group(0))
                rationale.append(f"matched[support]: {m.group(0).strip()}")

        # Rule: if NO core match and only one support match → reject.
        # This is the primary false-positive guard (e.g. a single stray
        # "consumer" mention should never classify as consumer_demand).
        if not core_hits:
            if len(support_hits) < 2:
                return None

        # --- Direction ------------------------------------------------
        direction = self._infer_direction(text, factor_key)

        # --- Magnitude (and numeric bonus) ----------------------------
        magnitude, numeric_bonus, numeric_rationale = self._infer_magnitude(
            text, factor_key
        )
        rationale.extend(numeric_rationale)

        # --- Confidence formula ---------------------------------------
        # p_factor = clamp(0.35 + 0.12*w_core + 0.06*w_support
        #                  + bonus_numeric - pen_uncertainty
        #                  - pen_ambiguity, 0.05, 0.95)
        w_core = min(len(core_hits), 4)
        w_support = min(len(support_hits), 4)
        pen_uncertainty = 0.0 if core_hits else 0.10  # support-only is weaker
        pen_ambiguity = 0.0
        if direction == "unknown":
            pen_ambiguity += 0.05
        if magnitude == "unknown":
            pen_ambiguity += 0.03

        confidence = (
            0.35
            + 0.12 * w_core
            + 0.06 * w_support
            + numeric_bonus
            - pen_uncertainty
            - pen_ambiguity
        )
        confidence = max(0.05, min(0.95, round(confidence, 4)))

        # Title-boost: if a core pattern hits inside the title itself
        # (not just the combined text), nudge confidence upward a bit.
        if core_hits and title:
            for pat in _COMPILED[factor_key]["core"]:
                if pat.search(title):
                    confidence = min(0.95, round(confidence + 0.03, 4))
                    break

        return FactorClassification(
            factor=factor_key,
            direction=direction,
            magnitude=magnitude,
            confidence=confidence,
            rationale=rationale[:10],  # cap rationale for storage
        )

    # ------------------------------------------------------------------
    # Direction inference
    # ------------------------------------------------------------------

    def _infer_direction(self, text: str, factor_key: str) -> str:
        """Return ``"up"``, ``"down"``, or ``"unknown"``.

        The rule is majority-of-cues over the *whole* text.  A handful
        of factor-specific shortcuts override the generic vote when
        present (e.g. "ceasefire" → geopolitical_risk down).

        Phase 9C corrective pass: oil/energy and trade_policy now have
        explicit, ordered direction rules so supply-cut language maps
        to ``oil_energy=up`` and easing/removal language maps to
        ``trade_policy=down`` reliably.  Easing branches are checked
        BEFORE restrictive branches so they stay reachable.
        """
        lowered = text.lower()

        # ----- Factor-specific overrides (checked first) ---------------
        if factor_key == "geopolitical_risk":
            if re.search(r"\b(?:ceasefire|truce|de[-\s]?escalat\w*)\b", lowered):
                return "down"
            if re.search(
                r"\b(?:escalat\w*|invade\w*|invasion|missile|airstrike|drone strike)\b",
                lowered,
            ):
                return "up"

        if factor_key == "oil_energy":
            # Phase 9C fix #1: OPEC / supply cut and physical supply
            # disruption language maps to oil_energy UP regardless of
            # the word "cut" appearing in the global down-cue list.
            # Order: check the easing/down branch first so future
            # "production increase" / "output boost" cases remain
            # reachable; then the restrictive/up branch; then fall
            # back to the generic vote.
            if re.search(
                r"\b(?:production|output|supply)\s+(?:increase|increases|boost|boosts|hike|hikes|expansion)\b",
                lowered,
            ):
                return "down"
            if re.search(
                r"\b(?:production|output|supply)\s+cut(?:s)?\b",
                lowered,
            ):
                return "up"
            if re.search(
                r"\b(?:pipeline|refiner(?:y|ies)|terminal|oilfield|wellhead)\s+"
                r"(?:attack|strike|fire|explosion|shutdown|outage|disruption)\b",
                lowered,
            ):
                return "up"
            if re.search(
                r"\b(?:opec\+?|saudi|russia)\s+(?:cut|cuts|reduce|reduces|curb|curbs|trim|trims)\b",
                lowered,
            ):
                return "up"
            if re.search(
                r"\b(?:opec\+?|saudi|russia)\s+(?:raise|raises|boost|boosts|hike|hikes|lift|lifts)\b",
                lowered,
            ):
                return "down"

        if factor_key == "trade_policy":
            # Phase 9C fix #2: the easing branch is now checked FIRST
            # so "sanctions lifted" / "tariff relief" can reach it
            # before the generic restrictive keyword catches them.
            if re.search(
                r"\b(?:tariff|duties)\s+(?:relief|cut|cuts|reduction|reductions|"
                r"rollback|rollbacks|reduced|lowered|lifted)\b",
                lowered,
            ):
                return "down"
            if re.search(
                r"\bsanction(?:s)?\s+(?:lifted|removed|eased|relief|rollback|rolled\s+back)\b",
                lowered,
            ):
                return "down"
            if re.search(
                r"\b(?:embargo(?:es)?\s+(?:lifted|removed|eased))\b",
                lowered,
            ):
                return "down"
            if re.search(
                r"\b(?:export\s+control(?:s)?|export\s+restrictions?|import\s+ban(?:s)?|export\s+ban(?:s)?)"
                r"\s+(?:eased|relaxed|lifted|removed|rolled\s+back)\b",
                lowered,
            ):
                return "down"
            if re.search(
                r"\btrade\s+restrictions?\s+(?:eased|lifted|removed|relaxed|rolled\s+back)\b",
                lowered,
            ):
                return "down"
            # Restrictive branch: only after the easing branches above.
            if re.search(
                r"\b(?:tariff|duties|sanction|embargo|export control|export ban|import ban)",
                lowered,
            ):
                return "up"

        # Generic: count up/down cue hits.
        up_hits = sum(1 for cue in _UP_CUES if re.search(rf"\b{re.escape(cue)}\b", lowered))
        down_hits = sum(1 for cue in _DOWN_CUES if re.search(rf"\b{re.escape(cue)}\b", lowered))

        if up_hits > down_hits and up_hits >= 1:
            return "up"
        if down_hits > up_hits and down_hits >= 1:
            return "down"
        return "unknown"

    # ------------------------------------------------------------------
    # Magnitude inference
    # ------------------------------------------------------------------

    def _infer_magnitude(
        self, text: str, factor_key: str
    ) -> tuple[str, float, list[str]]:
        """Return (magnitude_label, confidence_bonus, rationale_items).

        Numeric anchors (bps for rates, % for inflation/fx/oil) are
        the strongest signal; extreme amplifiers like "record" or
        "crisis" promote the label by one step.
        """
        rationale: list[str] = []
        bonus = 0.0
        magnitude = "unknown"
        lowered = text.lower()

        # --- BPS anchor (interest_rate) ---
        if factor_key == "interest_rate":
            bps_match = _RE_BPS.search(text)
            if bps_match:
                try:
                    bps = float(bps_match.group(1))
                except ValueError:
                    bps = 0.0
                rationale.append(f"parsed: {bps_match.group(0)}")
                if bps >= 75:
                    magnitude = "extreme"
                    bonus += 0.18
                elif bps >= 50:
                    magnitude = "major"
                    bonus += 0.12
                elif bps >= 25:
                    magnitude = "moderate"
                    bonus += 0.06
                elif bps > 0:
                    magnitude = "minor"
                    bonus += 0.02

        # --- Percentage anchor (inflation / oil / fx / consumer_demand) ---
        if factor_key in ("inflation", "oil_energy", "usd_fx", "consumer_demand"):
            pct_match = _RE_PCT.search(text)
            if pct_match:
                try:
                    pct = float(pct_match.group(1))
                except ValueError:
                    pct = 0.0
                rationale.append(f"parsed: {pct_match.group(0)}")
                # Scale differs by factor — inflation MoM prints are
                # small numbers, oil/fx moves are larger.
                if factor_key == "inflation":
                    if pct >= 1.0:
                        magnitude = "extreme"
                        bonus += 0.15
                    elif pct >= 0.5:
                        magnitude = "major"
                        bonus += 0.10
                    elif pct >= 0.3:
                        magnitude = "moderate"
                        bonus += 0.05
                    elif pct > 0:
                        magnitude = "minor"
                        bonus += 0.02
                else:
                    # oil, fx, consumer: typical daily/weekly moves
                    if pct >= 10:
                        magnitude = "extreme"
                        bonus += 0.15
                    elif pct >= 5:
                        magnitude = "major"
                        bonus += 0.10
                    elif pct >= 2:
                        magnitude = "moderate"
                        bonus += 0.05
                    elif pct > 0:
                        magnitude = "minor"
                        bonus += 0.02

        # --- Amplifier promotion ---
        if any(a in lowered for a in _EXTREME_AMPLIFIERS):
            rationale.append("parsed: extreme amplifier")
            promotion = {
                "unknown": "major",
                "minor": "moderate",
                "moderate": "major",
                "major": "extreme",
                "extreme": "extreme",
            }
            magnitude = promotion.get(magnitude, "major")
            bonus += 0.05

        return magnitude, bonus, rationale
