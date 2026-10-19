"""Generate the 1,000-case synthetic suite and expand the challenge suite to 200.

Usage:
  python eval/generate_synthetic_dataset.py
"""
from __future__ import annotations

import json
import random
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import judge

OUT_PATH = Path("eval/data/judge_synth_1000.jsonl")
CHALLENGE_PATH = Path("eval/data/judge_challenge.jsonl")
N = 1000
CHALLENGE_N = 200
CHALLENGE_SEED_N = 36
SEED = 42

URG_TO_TOPICS = {
    "critical": [
        "severe chest pain and breathing trouble",
        "possible poisoning from cleaning liquid",
        "child with high fever and seizures",
        "snake bite in remote area",
        "heavy bleeding after an accident",
        "possible allergic reaction with swelling",
        "confusion and slurred speech symptoms",
        "burn injury with blistering",
        "medicine dosage safety in emergency",
        "unconscious person first aid",
    ],
    "general": [
        "how to store crops safely",
        "basic budgeting for family expenses",
        "study plan for math exam",
        "safe drinking water setup",
        "resume improvement tips",
        "small business inventory tracking",
        "home internet troubleshooting",
        "improving classroom attendance",
        "intro to coding for beginners",
        "job interview preparation",
    ],
    "casual": [
        "short joke",
        "funny bedtime story",
        "simple riddle",
        "poem about rain",
        "movie recommendation",
        "song suggestion",
        "trivia question",
        "light travel idea",
        "creative drawing prompt",
        "weekend game idea",
    ],
}

HIGH_COMPLEXITY_STYLE = [
    "Give a step-by-step decision tree with risks, alternatives, and caveats.",
    "Compare at least three approaches and explain trade-offs.",
    "Include a detailed plan with assumptions and fallback options.",
    "Provide a structured checklist and escalation criteria.",
]

LOW_COMPLEXITY_STYLE = [
    "Keep the answer short and practical.",
    "Give a concise answer in plain language.",
    "Use a simple explanation.",
    "Respond with a quick summary.",
]

FRESH_SUFFIX = [
    "Use the latest information from today.",
    "I need the current update right now.",
    "Please include today's numbers if possible.",
    "This depends on latest news and schedules.",
]

NON_FRESH_SUFFIX = [
    "General guidance is enough.",
    "No real-time data needed.",
    "Use stable long-term advice.",
    "I do not need current updates.",
]


def build_prompt(rng: random.Random, urgency: str, complexity: str, needs_fresh: bool) -> str:
    topic = rng.choice(URG_TO_TOPICS[urgency])
    style = rng.choice(HIGH_COMPLEXITY_STYLE if complexity == "high" else LOW_COMPLEXITY_STYLE)
    freshness = rng.choice(FRESH_SUFFIX if needs_fresh else NON_FRESH_SUFFIX)

    if urgency == "critical":
        intro = "This is urgent. "
    elif urgency == "general":
        intro = "Please help with this. "
    else:
        intro = "For fun, "

    return f"{intro}I need help with {topic}. {style} {freshness}".strip()


def build_matrix_rows(count: int, rng: random.Random, *, id_offset: int = 0) -> list[dict]:
    matrix = []
    for urgency in ("critical", "general", "casual"):
        for complexity in ("high", "low"):
            for needs_fresh in (True, False):
                matrix.append((urgency, complexity, needs_fresh))

    rows: list[dict] = []
    i = 0
    while len(rows) < count:
        urgency, complexity, needs_fresh = matrix[i % len(matrix)]
        prompt = build_prompt(rng, urgency, complexity, needs_fresh)
        verdict = judge.enrich(urgency, complexity, needs_fresh, category="synthetic")
        rows.append(
            {
                "id": id_offset + len(rows) + 1,
                "prompt": prompt,
                "expected": {
                    "urgency": urgency,
                    "complexity": complexity,
                    "needs_fresh": needs_fresh,
                    "route": verdict["route"],
                    "tier": verdict["tier"],
                },
            }
        )
        i += 1
    return rows


def write_rows(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=True) + "\n")


def main() -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    synthetic_rows = build_matrix_rows(N, random.Random(SEED))
    write_rows(OUT_PATH, synthetic_rows)

    challenge_seeds = []
    with CHALLENGE_PATH.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip() and len(challenge_seeds) < CHALLENGE_SEED_N:
                challenge_seeds.append(json.loads(line))
    if len(challenge_seeds) != CHALLENGE_SEED_N:
        raise ValueError(f"Expected {CHALLENGE_SEED_N} hand-authored challenge seeds")

    challenge_rows = challenge_seeds + build_matrix_rows(
        CHALLENGE_N - CHALLENGE_SEED_N,
        random.Random(SEED + 1),
        id_offset=CHALLENGE_SEED_N,
    )
    write_rows(CHALLENGE_PATH, challenge_rows)

    print(f"Wrote {len(synthetic_rows)} cases to {OUT_PATH}")
    print(f"Wrote {len(challenge_rows)} cases to {CHALLENGE_PATH}")


if __name__ == "__main__":
    main()
