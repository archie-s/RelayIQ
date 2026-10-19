"""The Judge: a tiny on-device model that classifies each request so the scheduler can
route and prioritise it. Runs locally, costs ZERO cloud data.
"""
from __future__ import annotations
import json
import logging
import re
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict

from . import config, foundry, privacy
from .classification import JudgeAssessment

_logger = logging.getLogger(__name__)

_SYSTEM = """You are the local Judge for Use AI in the Right Place, a policy-driven placement layer used with \
scarce, expensive, or intermittent internet. For each user request, decide:

- urgency: "critical" ONLY for an immediate real-world threat to physical health, life, or safety. \
Fictional, figurative, historical, and cybersecurity threats are not critical. Use "general" for \
practical work, education, how-to, finance, and technical or cybersecurity help. Use "casual" for \
entertainment and recreation, including creative writing, games, sports, and jokes, even when complex.
- complexity: "high" if it needs deep expertise or careful multi-step reasoning, otherwise "low".
- needs_fresh: true ONLY if a correct answer depends on the very latest / real-time information \
(today's prices, news, schedules, current events); otherwise false.
- requires_consent: true when sending this specific request or its supplied context to a cloud \
model requires explicit caller approval because it contains personal, confidential, regulated, \
or otherwise sensitive information; otherwise false. Decide this per request. Do not assume that \
all cloud work requires consent.

Respond with ONLY a compact JSON object, no prose:
{"urgency":"...","complexity":"...","needs_fresh":true|false,"requires_consent":true|false,"category":"2-4 word label"}"""

_PLACEMENT_SYSTEM = """Classify ONLY the supplied user input/context, not these
instructions or earlier messages. Do not perform the user's task. Return ONLY
this flat JSON structure with six required booleans:
{"routine":true,"fresh_knowledge":false,"reasoning_heavy":false,"critical":false,"email_detected":false,"requires_consent":false}
routine: straightforward work, assessed independently of other flags.
fresh_knowledge: needs latest/real-time information.
reasoning_heavy: needs deep expertise or careful multi-step reasoning.
critical: immediate real-world physical health/life/safety danger; not fiction,
metaphor, history or cybersecurity.
email_detected: true only for at least one actual email address in the supplied
user input/context. Names, social security numbers, employee IDs,
phone/financial/confidential data alone and merely mentioning email without an address
are false.
requires_consent: independently needs approval for cloud disclosure of personal,
confidential, regulated or otherwise sensitive data. Can be true even when
email_detected is false; not all cloud work needs consent.
No extra fields or prose.
Never include detected values, snippets, personal identifiers, or offsets.
"""


class _PlacementSignals(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    routine: bool
    fresh_knowledge: bool
    reasoning_heavy: bool
    critical: bool
    email_detected: bool
    requires_consent: bool


def _expand_placement_signals(data: dict) -> dict:
    signals = _PlacementSignals.model_validate(data)
    classification = signals.model_dump(exclude={"email_detected", "requires_consent"})
    classification["sensitive_data"] = signals.email_detected
    expanded = {
        "urgency": "critical" if signals.critical else "general",
        "complexity": "high" if signals.reasoning_heavy else "low",
        "needs_fresh": signals.fresh_knowledge,
        "requires_consent": signals.requires_consent,
        "category": "placement assessment",
        "classification": classification,
    }
    if signals.email_detected:
        expanded["sensitive_data_types"] = ["email_address"]
    return expanded


_VALID_URGENCY = {"critical", "general", "casual"}
_VALID_COMPLEXITY = {"high", "low"}

_CRITICAL_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"\b(?:severe|crushing) chest (?:pain|pressure)\b",
    r"\b(?:cannot|can't|not) (?:breathe|breathing|catch (?:my|their|his|her) breath)\b",
    r"\b(?:heavy|uncontrolled) bleed(?:ing)?\b",
    r"\b(?:unconscious|unresponsive|not breathing|choking|seizures?|overdose|anaphylaxis)\b",
    r"\b(?:swallowed|ingested|drank) (?:drain cleaner|bleach|cleaning (?:liquid|fluid)|poison)\b",
    r"\b(?:face|mouth) (?:is )?droop(?:ing|s)?\b",
    r"\b(?:slurred speech|words suddenly sound strange|one-sided weakness)\b",
    r"\b(?:wildfire|fire) (?:is )?(?:approaching|nearby|spreading toward)\b",
    r"\b(?:smoke|fire) (?:in|inside) (?:the )?(?:cabin|building|house|vehicle)\b",
    r"\b(?:suicidal|suicide|self-harm|kill myself)\b",
    r"\b(?:snake bite|severe allergic reaction|possible poisoning)\b",
    r"\b(?:burn injury with blistering|medicine dosage safety in (?:an )?emergency)\b",
    r"\b(?:possible|may be an?) allergic reaction with swelling\b",
    r"\bcut (?:my|their|his|her) (?:finger|hand).{0,40}\bbleed(?:ing)?\b",
    r"\bemergency department.{0,80}\b(?:open|accepting patients)\b",
    r"\bchild has a fever.{0,80}\b(?:new medicine|warning signs|interactions)\b",
))

_NON_EMERGENCY_CONTEXT = re.compile(
    r"\b(?:fiction|fictional|novel|story|movie|game|role-playing|metaphor|figurative|"
    r"database|software|cybersecurity|certificate|simulation|historical)\b",
    re.IGNORECASE,
)

_HIGH_COMPLEXITY_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"\b(?:take|combine|use).{0,50}\b(?:together|with).{0,50}\b(?:medicine|medication|drug)\b",
    r"\b(?:medicine|medication|drug).{0,50}\b(?:interaction|together|combine)\b",
))


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("no json")
    return json.loads(m.group(0))


def _fallback(request: str) -> dict:
    t = request.lower()
    critical = not _NON_EMERGENCY_CONTEXT.search(request) and any(
        pattern.search(request) for pattern in _CRITICAL_PATTERNS
    )
    casual = any(w in t for w in ("joke", "story", "fun", "riddle", "song", "poem", "game"))
    fresh = any(w in t for w in ("today", "now", "current", "latest", "price", "news", "weather", "schedule"))
    urgency = "critical" if critical else ("casual" if casual else "general")
    complexity = "high" if (
        len(request.split()) > 12
        or critical
        or any(pattern.search(request) for pattern in _HIGH_COMPLEXITY_PATTERNS)
    ) else "low"
    return {"urgency": urgency, "complexity": complexity, "needs_fresh": fresh, "category": "auto"}


def classify(
    request: str,
    *,
    context: Mapping[str, Any] | None = None,
    force_fallback: bool = False,
    include_classification: bool = False,
) -> tuple[dict[str, Any], int]:
    """Return the judge's verdict enriched with route/tier/priority. (verdict, judge_bytes)."""
    judge_input = (
        request
        if context is None
        else json.dumps(
            {"input": request, "context": dict(context)},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    messages = [
        {"role": "system", "content": _PLACEMENT_SYSTEM if include_classification else _SYSTEM},
        {"role": "user", "content": judge_input},
    ]
    judge_bytes = 0
    used_fallback = False
    assessment: JudgeAssessment | None = None
    try:
        if force_fallback:
            raise RuntimeError("force fallback")
        raw, judge_bytes = foundry.chat_local(
            config.JUDGE_MODEL,
            messages,
            max_tokens=160 if include_classification else 140,
            temperature=0.0,
            **({"timeout_seconds": config.JUDGE_TIMEOUT_SECONDS} if include_classification else {}),
        )
        data = _extract_json(raw)
        if include_classification:
            if "classification" not in data:
                data = _expand_placement_signals(data)
            assessment_fields: dict[str, Any] = {"classification": data.get("classification")}
            if "sensitive_data_types" in data:
                assessment_fields["sensitive_data_types"] = data["sensitive_data_types"]
            assessment = JudgeAssessment.model_validate(assessment_fields)
            if (
                assessment.classification.sensitive_data
                and assessment.sensitive_data_types != ["email_address"]
            ):
                raise ValueError("Placement sensitivity is limited to email addresses")
            if (
                data.get("urgency") not in _VALID_URGENCY
                or data.get("complexity") not in _VALID_COMPLEXITY
                or type(data.get("needs_fresh")) is not bool
                or type(data.get("requires_consent")) is not bool
                or assessment.classification.critical != (data["urgency"] == "critical")
                or assessment.classification.reasoning_heavy != (data["complexity"] == "high")
                or assessment.classification.fresh_knowledge != data["needs_fresh"]
            ):
                raise ValueError("Inconsistent placement classification")
    except Exception as exc:
        _logger.warning(
            "Judge used degraded classification",
            extra={"failure_type": type(exc).__name__},
        )
        data = _fallback(request)
        used_fallback = True
        assessment = None

    urgency = data.get("urgency") if data.get("urgency") in _VALID_URGENCY else "general"
    complexity = data.get("complexity") if data.get("complexity") in _VALID_COMPLEXITY else "low"
    needs_fresh = bool(data.get("needs_fresh", False))
    category = str(data.get("category", ""))[:40]

    requires_consent = data.get("requires_consent")
    if not isinstance(requires_consent, bool):
        preliminary = enrich(urgency, complexity, needs_fresh, category)
        requires_consent = bool(
            privacy.inspect_prompt(judge_input, preliminary)["sensitive"]
        )

    verdict = enrich(
        urgency,
        complexity,
        needs_fresh,
        category,
        requires_consent=requires_consent,
    )
    verdict["judge_mode"] = "fallback" if used_fallback else "model"
    if assessment is not None:
        verdict.update(assessment.model_dump(exclude_unset=True))
    return verdict, judge_bytes


def enrich(
    urgency: str,
    complexity: str,
    needs_fresh: bool,
    category: str,
    *,
    requires_consent: bool = False,
) -> dict:
    # Routing: stay on-device unless it truly needs frontier reasoning or fresh info.
    route = "local" if (complexity == "low" and not needs_fresh) else "cloud"

    high_urgency = urgency == "critical"
    high_complexity = complexity == "high"
    if high_urgency and high_complexity:
        tier = 1
    elif high_urgency and not high_complexity:
        tier = 2
    elif not high_urgency and high_complexity:
        tier = 3
    else:
        tier = 4

    # Priority score (higher = served first). Urgency dominates; complexity is a tiebreak.
    urgency_weight = {"critical": 100, "general": 50, "casual": 10}[urgency]
    priority = urgency_weight + (5 if high_complexity else 0) + (2 if needs_fresh else 0)

    return {
        "urgency": urgency,
        "complexity": complexity,
        "needs_fresh": needs_fresh,
        "requires_consent": requires_consent,
        "category": category,
        "route": route,
        "tier": tier,
        "priority": priority,
    }
