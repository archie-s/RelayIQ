"""The Scheduler: routes each request, answers cheap ones locally for free, and drains the
scarce cloud queue by priority within a data budget — only when there is connectivity.
"""
from __future__ import annotations
import asyncio
import itertools
import logging
import time
from typing import Awaitable, Callable

from . import config, foundry, policy
from .placement import Placement, PlacementContext, PlacementEngine

_ids = itertools.count(1)
_logger = logging.getLogger(__name__)

_ANSWER_SYS = (
    "You are a helpful assistant for someone with limited connectivity. Answer clearly and "
    "concisely in plain language. Be practical. If a real professional is needed, say so briefly."
)


class Job:
    def __init__(self, text: str):
        self.id = next(_ids)
        self.text = text
        self.cloud_text = text
        self.status = "judging"      # judging|running-local|answered|answered-offline|queued|deferred|awaiting-approval|blocked-local|running-cloud|cloud-error|done
        self.verdict: dict | None = None
        self.privacy: dict | None = None
        self.answer = ""
        self.local_bytes = 0
        self.cloud_bytes = 0
        self.is_real_cloud = False
        self.pending_cloud = False   # answered on-device during blackout; will refine via cloud when online
        self.cloud_approved = False
        self.provisional_task: asyncio.Task | None = None
        self.created = time.time()

    def to_dict(self, connectivity: bool) -> dict:
        d = {
            "id": self.id, "text": self.text, "status": self.status,
            "answer": self.answer, "cloud_bytes": self.cloud_bytes,
            "is_real_cloud": self.is_real_cloud, "pending_cloud": self.pending_cloud,
        }
        if self.verdict:
            d.update({k: self.verdict[k] for k in
                      ("urgency", "complexity", "needs_fresh", "category", "route", "tier", "priority")})
        if self.privacy:
            d.update({
                "privacy_sensitive": self.privacy["sensitive"],
                "privacy_types": self.privacy["types"],
                "privacy_requires_review": self.privacy["requires_human_review"],
                "privacy_emergency_override": self.privacy["emergency_override"],
                "privacy_reason": self.privacy["reason"],
                "privacy_redacted_prompt": self.privacy["redacted_prompt"],
            })
        if self.verdict and self.privacy:
            d["decision"] = policy.decision_record(
                self.verdict,
                self.privacy,
                self.status,
                connectivity,
                self.is_real_cloud,
            )
            estimated_kb = (len(self.cloud_text.encode("utf-8")) + 1200) / 1024
            d["decision"]["estimated_cloud_cost_usd"] = round(
                config.EST_USD_PER_CLOUD_REQ + estimated_kb * config.EST_USD_PER_KB,
                4,
            )
        return d


class Scheduler:
    def __init__(self, broadcast: Callable[[], Awaitable[None]]):
        self._broadcast = broadcast
        self._placement = PlacementEngine()
        self.jobs: dict[int, Job] = {}
        self.connectivity = True
        self.budget_kb = config.DEFAULT_BUDGET_KB
        self.cloud_kb_used = 0.0
        self.naive_kb = 0.0            # bytes if EVERY request had gone to the cloud
        self.local_only_count = 0      # requests fully resolved locally (never used cloud)
        self._cloud_q: list[Job] = []
        self._worker: asyncio.Task | None = None

    # ---------- lifecycle ----------
    def start(self):
        if not self._worker:
            self._worker = asyncio.create_task(self._drain_cloud())

    # ---------- public commands ----------
    async def submit(self, text: str) -> None:
        job = Job(text)
        self.jobs[job.id] = job
        await self._push()
        asyncio.create_task(self._handle(job))

    async def set_connectivity(self, value: bool) -> None:
        self.connectivity = value
        if not value:
            # Blackout: any request still waiting for the cloud gets an on-device
            # fallback answer now, so nobody is stranded. It stays queued to be
            # refined by the cloud when the signal returns.
            for job in list(self._cloud_q):
                if job.status in ("queued", "deferred") and not job.pending_cloud:
                    if (
                        job.privacy
                        and job.privacy["sensitive"]
                        and not job.cloud_approved
                    ):
                        self._cloud_q.remove(job)
                        asyncio.create_task(self._answer_local(job))
                    else:
                        asyncio.create_task(self._answer_local(job, offline_fallback=True))
        else:
            self._promote_sensitive_deferred()
        await self._push()

    async def set_budget(self, kb: float) -> None:
        self.budget_kb = max(0.0, float(kb))
        self._promote_sensitive_deferred()
        await self._push()

    async def reset(self) -> None:
        self.jobs.clear()
        self._cloud_q.clear()
        self.cloud_kb_used = 0.0
        self.naive_kb = 0.0
        self.local_only_count = 0
        await self._push()

    async def review(self, job_id: int, allow_cloud: bool) -> None:
        job = self.jobs.get(job_id)
        if not job or job.status != "awaiting-approval":
            return

        if allow_cloud:
            job.cloud_text = job.privacy["redacted_prompt"]
            job.cloud_approved = True
            job.status = "queued"
            self._cloud_q.append(job)
            await self._push()
            return

        if job.provisional_task:
            await job.provisional_task
        if not job.answer:
            await self._answer_local(job)
        else:
            self.naive_kb += job.local_bytes / 1024
            self.local_only_count += 1
        job.pending_cloud = False
        job.status = "blocked-local"
        await self._push()

    # ---------- internals ----------
    async def _handle(self, job: Job) -> None:
        decision = await asyncio.to_thread(
            self._placement.decide,
            job.text,
            PlacementContext(
                connectivity=self.connectivity,
                cloud_budget_kb=max(0.0, self.budget_kb - self.cloud_kb_used),
            ),
        )
        job.verdict = decision.verdict
        job.privacy = decision.privacy
        job.cloud_text = job.privacy["redacted_prompt"]
        await self._push()

        if job.verdict["route"] == "local":
            # Cheap request: on-device model answers it, free, online or offline.
            await self._answer_local(job)
            return

        # Cloud-worthy request (deep reasoning or fresh info needed).
        if decision.requires_consent:
            job.status = "awaiting-approval"
            await self._push()
            if job.privacy.get("emergency_override"):
                job.pending_cloud = True
                job.provisional_task = asyncio.create_task(self._answer_provisional(job))
            return

        if decision.placement == Placement.LOCAL:
            # BLACKOUT: no cloud reachable. The on-device model answers best-effort now.
            # Non-sensitive work can remain queued for refinement after reconnection.
            if job.privacy["sensitive"]:
                await self._answer_local(job)
            else:
                await self._answer_local(job, offline_fallback=True)
                self._cloud_q.append(job)
        elif decision.placement == Placement.DEFERRED:
            job.status = "deferred"
            self._cloud_q.append(job)
            await self._push()
        else:
            job.status = "queued"
            self._cloud_q.append(job)
            await self._push()

    async def _answer_local(self, job: Job, offline_fallback: bool = False) -> None:
        """Answer a job with the on-device model. Costs 0 cloud data."""
        job.status = "running-local"
        await self._push()
        answer, wire = await asyncio.to_thread(
            foundry.chat_local, config.LOCAL_MODEL,
            [{"role": "system", "content": _ANSWER_SYS}, {"role": "user", "content": job.text}],
        )
        job.answer = answer
        job.local_bytes = wire
        if offline_fallback:
            # This request will still go to the cloud later, so don't count it as "saved"
            # here — the byte accounting happens when the cloud refines it.
            job.status = "answered-offline"
            job.pending_cloud = True
        else:
            job.status = "answered"
            # On-device answer costs 0 cloud data; naive baseline would have paid for it.
            self.naive_kb += wire / 1024
            self.local_only_count += 1
        await self._push()

    async def _answer_provisional(self, job: Job) -> None:
        """Generate local emergency guidance without hiding the approval controls."""
        answer, wire = await asyncio.to_thread(
            foundry.chat_local, config.LOCAL_MODEL,
            [{"role": "system", "content": _ANSWER_SYS}, {"role": "user", "content": job.text}],
        )
        if job.status not in ("done", "cloud-error", "blocked-local"):
            job.answer = answer
            job.local_bytes = wire
        await self._push()

    async def _drain_cloud(self) -> None:
        while True:
            job = self._next_cloud_job()
            if job is None:
                await asyncio.sleep(0.4)
                continue
            job.status = "running-cloud"
            await self._push()
            try:
                answer, wire, is_real = await asyncio.to_thread(
                    foundry.chat_cloud,
                    [{"role": "system", "content": _ANSWER_SYS}, {"role": "user", "content": job.cloud_text}],
                    use_web_search=bool(job.verdict["needs_fresh"]),
                )
            except Exception:
                _logger.exception("Cloud request failed for job %s", job.id)
                job.status = "cloud-error"
                job.answer = "The cloud model is temporarily unavailable. Retry after authentication or connectivity is restored."
                await self._push()
                continue
            job.answer = answer
            job.cloud_bytes = wire
            job.is_real_cloud = is_real
            job.status = "done"
            job.pending_cloud = False
            self.cloud_kb_used += wire / 1024
            self.naive_kb += wire / 1024
            await self._push()

    def _next_cloud_job(self) -> Job | None:
        """Highest-priority queued job that fits the remaining budget, if connected."""
        if not self.connectivity or not self._cloud_q:
            return None
        if any(job.status == "judging" for job in self.jobs.values()):
            return None
        remaining = self.budget_kb - self.cloud_kb_used
        # Estimate a job's cost from its prompt (answer unknown until we call).
        candidates = sorted(
            (
                job
                for job in self._cloud_q
                if job.status in ("queued", "deferred", "answered-offline")
                and not (
                    job.status == "deferred"
                    and job.privacy
                    and job.privacy["requires_human_review"]
                    and not job.cloud_approved
                )
            ),
            key=lambda job: (
                bool(job.privacy and job.privacy.get("emergency_override")),
                job.verdict["priority"],
                -job.created,
            ),
            reverse=True,
        )
        for job in candidates:
            est_kb = (len(job.text.encode("utf-8")) + 1200) / 1024  # prompt + typical answer
            if est_kb <= remaining:
                self._cloud_q.remove(job)
                return job
        # Nothing fits the budget -> mark the queue as deferred (still visible).
        for job in self._cloud_q:
            if job.status == "queued":
                job.status = "deferred"
        return None

    def _promote_sensitive_deferred(self) -> None:
        if not self.connectivity:
            return
        remaining = self.budget_kb - self.cloud_kb_used
        for job in list(self._cloud_q):
            if not (
                job.status == "deferred"
                and job.privacy
                and job.privacy["requires_human_review"]
                and not job.cloud_approved
            ):
                continue
            estimated_kb = (len(job.cloud_text.encode("utf-8")) + 1200) / 1024
            if estimated_kb <= remaining:
                self._cloud_q.remove(job)
                job.status = "awaiting-approval"

    # ---------- state ----------
    def snapshot(self) -> dict:
        saved = max(0.0, self.naive_kb - self.cloud_kb_used)
        pct = (saved / self.naive_kb * 100) if self.naive_kb > 0 else 0.0
        saved_usd = saved * config.EST_USD_PER_KB + self.local_only_count * config.EST_USD_PER_CLOUD_REQ
        saved_eur = saved_usd * config.USD_TO_EUR
        saved_co2g = saved * config.EST_CO2G_PER_KB + self.local_only_count * config.EST_CO2G_PER_CLOUD_REQ
        completed_cloud = sum(1 for job in self.jobs.values() if job.cloud_bytes > 0)
        resolved = self.local_only_count + completed_cloud
        cloud_avoided_pct = (self.local_only_count / resolved * 100) if resolved else 0.0
        private_markers = sum(
            len(job.privacy["types"])
            for job in self.jobs.values()
            if job.privacy and job.privacy["sensitive"]
        )
        private_retention_pct = 100.0 if private_markers else None
        efficiency_inputs = [pct, cloud_avoided_pct]
        if private_retention_pct is not None:
            efficiency_inputs.append(private_retention_pct)
        knowledge_efficiency = sum(efficiency_inputs) / len(efficiency_inputs)
        return {
            "connectivity": self.connectivity,
            "budget_kb": round(self.budget_kb, 1),
            "cloud_kb_used": round(self.cloud_kb_used, 2),
            "naive_kb": round(self.naive_kb, 2),
            "saved_kb": round(saved, 2),
            "saved_pct": round(pct, 1),
            "saved_usd": round(saved_usd, 4),
            "saved_eur": round(saved_eur, 4),
            "saved_co2g": round(saved_co2g, 2),
            "local_only_count": self.local_only_count,
            "cloud_calls_avoided_pct": round(cloud_avoided_pct, 1),
            "private_markers_retained": private_markers,
            "private_retention_pct": private_retention_pct,
            "knowledge_efficiency_score": round(knowledge_efficiency, 1),
            "knowledge_efficiency_formula": "Mean of bandwidth saved %, cloud calls avoided %, and private-marker retention % when detected",
            "impact_assumptions": {
                "usd_per_kb": config.EST_USD_PER_KB,
                "usd_per_cloud_request": config.EST_USD_PER_CLOUD_REQ,
                "co2g_per_kb": config.EST_CO2G_PER_KB,
                "co2g_per_cloud_request": config.EST_CO2G_PER_CLOUD_REQ,
                "usd_to_eur": config.USD_TO_EUR,
            },
            "cloud_is_real": config.cloud_is_real(),
            "local_model": config.LOCAL_MODEL,
            "cloud_model": config.AZURE_OPENAI_DEPLOYMENT,
            "cloud_sim_model": config.CLOUD_SIM_MODEL,
            "jobs": [self.jobs[i].to_dict(self.connectivity) for i in sorted(self.jobs)],
        }

    async def _push(self) -> None:
        await self._broadcast()
