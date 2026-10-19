"""Benchmark placement and end-to-end backend latency.

Usage:
  python eval/benchmark_latency.py
  python eval/benchmark_latency.py --core-runs 1 --api-runs 1 --execution-runs 1
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import platform
from pathlib import Path
from time import perf_counter
import sys
from urllib.parse import urlparse

import requests
from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config
from app.placement import PlacementContext, PlacementEngine
from eval.evaluate_judge import latency_summary

REPORT_JSON = Path("eval/reports/latency_benchmark_latest.json")
REPORT_MD = Path("eval/reports/latency_benchmark_latest.md")
PLACEMENT_PROMPT = "Explain photosynthesis in one sentence."
EXECUTION_PROMPT = "Explain photosynthesis to a ten-year-old in one sentence."
TERMINAL_STATUSES = {"answered", "answered-offline", "blocked-local", "cloud-error", "done"}


def benchmark_core(runs: int, warmup: bool) -> tuple[list[float], str]:
    engine = PlacementEngine()
    context = PlacementContext(connectivity=True, cloud_budget_kb=40)
    if warmup:
        engine.decide(PLACEMENT_PROMPT, context)

    durations = []
    judge_mode = "unknown"
    for _ in range(runs):
        started = perf_counter()
        decision = engine.decide(PLACEMENT_PROMPT, context)
        durations.append((perf_counter() - started) * 1000)
        judge_mode = decision.verdict.get("judge_mode", "unknown")
    return durations, judge_mode


def benchmark_api(base_url: str, runs: int, warmup: bool) -> list[float]:
    payload = {
        "input": PLACEMENT_PROMPT,
        "context": {"connectivity": True, "cloud_budget_kb": 40},
    }
    session = requests.Session()

    def invoke() -> float:
        started = perf_counter()
        response = session.post(
            f"{base_url}/v1/placement/decide",
            json=payload,
            timeout=120,
        )
        elapsed_ms = (perf_counter() - started) * 1000
        response.raise_for_status()
        response.json()
        return elapsed_ms

    if warmup:
        invoke()

    return [invoke() for _ in range(runs)]


def websocket_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return f"{scheme}://{parsed.netloc}/ws"


async def receive_until(websocket, predicate, timeout: float) -> dict:
    async with asyncio.timeout(timeout):
        while True:
            state = json.loads(await websocket.recv())
            if predicate(state):
                return state


async def execute_local_once(base_url: str, timeout: float) -> float:
    async with connect(websocket_url(base_url), max_size=2**20) as websocket:
        await websocket.recv()
        await websocket.send(json.dumps({"type": "reset"}))
        await receive_until(websocket, lambda state: not state.get("jobs"), timeout)

        started = perf_counter()
        await websocket.send(json.dumps({"type": "ask", "text": EXECUTION_PROMPT}))
        await receive_until(
            websocket,
            lambda state: any(
                job.get("text") == EXECUTION_PROMPT
                and job.get("status") in TERMINAL_STATUSES
                and bool(job.get("answer"))
                for job in state.get("jobs", [])
            ),
            timeout,
        )
        return (perf_counter() - started) * 1000


async def benchmark_execution(
    base_url: str,
    runs: int,
    warmup: bool,
    timeout: float,
) -> list[float]:
    if warmup:
        await execute_local_once(base_url, timeout)
    return [await execute_local_once(base_url, timeout) for _ in range(runs)]


def write_reports(report: dict) -> None:
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")

    latency = report["latency_ms"]
    lines = [
        "---",
        "title: Latency benchmark report",
        "description: Placement-only, placement API, and local execution latency measurements.",
        f"ms.date: {datetime.now(timezone.utc).date().isoformat()}",
        "ms.topic: reference",
        "---",
        "",
        "## Environment",
        "",
        f"* Timestamp: {report['timestamp_utc']}",
        f"* Judge mode: {report['judge_mode']}",
        f"* Local model: {report['local_model']}",
        f"* Cloud mode: {'real' if report['cloud_is_real'] else 'local simulation'}",
        f"* Machine: {report['environment']['machine']}",
        f"* Processor: {report['environment']['processor']}",
        f"* Graphics: {report['environment']['graphics']}",
        f"* Memory: {report['environment']['memory']}",
        f"* Operating system: {report['environment']['operating_system']}",
        f"* Python: {report['environment']['python']}",
        f"* Execution provider: {report['environment']['execution_provider']}",
        "",
        "## Results",
        "",
        "| Layer | Samples | Mean | P50 | P95 | Maximum |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for key, label in (
        ("placement_core_model_backed", "In-process placement"),
        ("placement_api_e2e", "Placement API end to end"),
        ("local_execution_e2e", "Local answer execution over WebSocket"),
    ):
        result = latency[key]
        lines.append(
            f"| {label} | {result['samples']} | {result['mean']:.1f} ms | "
            f"{result['p50']:.1f} ms | {result['p95']:.1f} ms | {result['max']:.1f} ms |"
        )

    lines += [
        "",
        "In-process placement includes local Judge inference, policy, and privacy.",
        "Placement API end to end additionally includes loopback HTTP, request validation,",
        "the worker-thread hop, response validation, and serialization. Local answer",
        "execution additionally includes WebSocket transport, scheduling, and Foundry Local",
        "answer generation. Browser rendering and real cloud execution are not included.",
        "The execution provider was not instrumented, so this is a machine-specific",
        "development observation rather than a CPU, GPU, or product performance claim.",
        "Controlled GPU benchmarks are still required.",
        "",
    ]
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")


def with_samples(values: list[float]) -> dict:
    return {"samples": len(values), **latency_summary(values)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--core-runs", type=int, default=5)
    parser.add_argument("--api-runs", type=int, default=5)
    parser.add_argument("--execution-runs", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--skip-warmup", action="store_true")
    parser.add_argument("--machine", default=platform.node() or "Not recorded")
    parser.add_argument("--processor", default=platform.processor() or platform.machine())
    parser.add_argument("--graphics", default="Not instrumented")
    parser.add_argument("--memory", default="Not recorded")
    parser.add_argument("--execution-provider", default="Not instrumented")
    args = parser.parse_args()
    warmup = not args.skip_warmup
    base_url = args.base_url.rstrip("/")

    core, core_mode = benchmark_core(args.core_runs, warmup)
    api = benchmark_api(base_url, args.api_runs, warmup)
    execution = asyncio.run(
        benchmark_execution(base_url, args.execution_runs, warmup, args.timeout)
    )

    report = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "judge_mode": core_mode,
        "local_model": config.LOCAL_MODEL,
        "cloud_is_real": config.cloud_is_real(),
        "environment": {
            "machine": args.machine,
            "processor": args.processor,
            "graphics": args.graphics,
            "memory": args.memory,
            "operating_system": platform.platform(),
            "python": platform.python_version(),
            "execution_provider": args.execution_provider,
        },
        "prompts": {
            "placement": PLACEMENT_PROMPT,
            "execution": EXECUTION_PROMPT,
        },
        "latency_ms": {
            "placement_core_model_backed": with_samples(core),
            "placement_api_e2e": with_samples(api),
            "local_execution_e2e": with_samples(execution),
        },
        "scope": {
            "placement_core_model_backed": "Local Judge inference, policy, and privacy in process.",
            "placement_api_e2e": "Loopback HTTP through validated placement response.",
            "local_execution_e2e": "WebSocket submission through completed Foundry Local answer.",
            "excluded": ["browser rendering", "real cloud execution", "network variability beyond loopback"],
        },
    }
    write_reports(report)
    print(json.dumps(report["latency_ms"], indent=2))
    print(f"Saved {REPORT_JSON}")
    print(f"Saved {REPORT_MD}")


if __name__ == "__main__":
    main()