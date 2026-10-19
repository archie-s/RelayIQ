from __future__ import annotations

import asyncio
import threading
import unittest
from unittest.mock import AsyncMock, patch

from app import judge, policy, privacy
from app.main import DEMO_BATCH
from app.scheduler import Job, Scheduler


class SchedulerPriorityTests(unittest.IsolatedAsyncioTestCase):
    async def test_emergency_approval_is_visible_before_local_answer_finishes(self) -> None:
        scheduler = Scheduler(AsyncMock())
        job = Job(DEMO_BATCH[0])
        scheduler.jobs[job.id] = job
        local_started = threading.Event()
        release_local = threading.Event()

        def slow_local(*args, **kwargs):
            local_started.set()
            release_local.wait(timeout=2)
            return "Immediate local guidance", 128

        verdict = judge.enrich("critical", "high", False, "medical emergency")
        try:
            with (
                patch("app.placement.judge.classify", return_value=(verdict, 0)),
                patch("app.scheduler.foundry.chat_local", side_effect=slow_local),
            ):
                handle = asyncio.create_task(scheduler._handle(job))
                self.assertTrue(await asyncio.to_thread(local_started.wait, 1))
                self.assertEqual("awaiting-approval", job.status)
                self.assertNotIn(job, scheduler._cloud_q)
                release_local.set()
                await handle
                if job.provisional_task:
                    await job.provisional_task
        finally:
            release_local.set()

    async def test_approved_emergency_outranks_equal_priority_cloud_work(self) -> None:
        scheduler = Scheduler(AsyncMock())
        regular = Job("Critical request")
        regular.verdict = judge.enrich("critical", "high", True, "medical")
        regular.privacy = {"emergency_override": False}
        regular.status = "queued"

        emergency = Job("Critical sensitive emergency")
        emergency.verdict = judge.enrich("critical", "high", True, "medical")
        emergency.privacy = {"emergency_override": True}
        emergency.status = "queued"

        scheduler._cloud_q.extend([regular, emergency])

        self.assertIs(emergency, scheduler._next_cloud_job())

    async def test_pending_emergency_approval_does_not_block_cloud_work(self) -> None:
        scheduler = Scheduler(AsyncMock())
        pending = Job(DEMO_BATCH[0])
        pending.verdict = judge.enrich("critical", "high", True, "medical")
        pending.privacy = privacy.inspect_prompt(pending.text, pending.verdict)
        pending.status = "awaiting-approval"

        diarrhea = Job(DEMO_BATCH[1])
        diarrhea.verdict = judge.enrich("critical", "high", True, "medical")
        diarrhea.privacy = privacy.inspect_prompt(diarrhea.text, diarrhea.verdict)
        diarrhea.status = "queued"
        scheduler._cloud_q.append(diarrhea)

        self.assertIs(diarrhea, scheduler._next_cloud_job())
        self.assertEqual("awaiting-approval", pending.status)

    async def test_offline_sensitive_cloud_work_stays_local_without_requesting_consent(self) -> None:
        scheduler = Scheduler(AsyncMock())
        scheduler.connectivity = False
        job = Job("Review employee id EMP-20431 and salary.")
        scheduler.jobs[job.id] = job
        verdict = judge.enrich("general", "high", False, "employee review")

        with (
            patch("app.placement.judge.classify", return_value=(verdict, 0)),
            patch("app.scheduler.foundry.chat_local", return_value=("Local answer", 128)),
        ):
            await scheduler._handle(job)

        self.assertEqual("answered", job.status)
        self.assertFalse(job.pending_cloud)
        self.assertNotIn(job, scheduler._cloud_q)

    async def test_deferred_sensitive_work_requests_consent_after_budget_increases(self) -> None:
        scheduler = Scheduler(AsyncMock())
        scheduler.budget_kb = 0
        job = Job("Review employee id EMP-20431 and salary.")
        scheduler.jobs[job.id] = job
        verdict = judge.enrich("general", "high", False, "employee review")

        with patch("app.placement.judge.classify", return_value=(verdict, 0)):
            await scheduler._handle(job)

        self.assertEqual("deferred", job.status)
        self.assertIn(job, scheduler._cloud_q)
        self.assertIsNone(scheduler._next_cloud_job())

        await scheduler.set_budget(40)

        self.assertEqual("awaiting-approval", job.status)
        self.assertNotIn(job, scheduler._cloud_q)

    async def test_approved_sensitive_work_is_not_reprompted_after_budget_deferral(self) -> None:
        scheduler = Scheduler(AsyncMock())
        job = Job("Sensitive approved request")
        job.verdict = judge.enrich("general", "high", False, "employee review")
        job.privacy = {"requires_human_review": True, "emergency_override": False}
        job.cloud_approved = True
        job.status = "queued"
        scheduler.jobs[job.id] = job
        scheduler._cloud_q.append(job)
        scheduler.budget_kb = 0

        self.assertIsNone(scheduler._next_cloud_job())
        self.assertEqual("deferred", job.status)

        await scheduler.set_budget(40)

        self.assertEqual("deferred", job.status)
        self.assertIs(job, scheduler._next_cloud_job())

    async def test_connectivity_change_during_classification_cannot_bypass_consent(self) -> None:
        scheduler = Scheduler(AsyncMock())
        scheduler.connectivity = False
        job = Job("Review employee id EMP-20431 and salary.")
        scheduler.jobs[job.id] = job
        verdict = judge.enrich("general", "high", False, "employee review")

        def restore_connectivity(*args, **kwargs):
            scheduler.connectivity = True
            return verdict, 0

        with (
            patch("app.placement.judge.classify", side_effect=restore_connectivity),
            patch("app.scheduler.foundry.chat_local", return_value=("Local answer", 128)),
        ):
            await scheduler._handle(job)

        self.assertEqual("answered", job.status)
        self.assertNotIn(job, scheduler._cloud_q)

    async def test_cloud_selection_waits_for_inflight_judging(self) -> None:
        scheduler = Scheduler(AsyncMock())
        judging = Job("Higher-priority request still being judged")
        scheduler.jobs[judging.id] = judging

        queued = Job("Lower-priority request ready first")
        queued.verdict = judge.enrich("general", "high", False, "medical")
        queued.privacy = privacy.inspect_prompt(queued.text, queued.verdict)
        queued.status = "queued"
        scheduler.jobs[queued.id] = queued
        scheduler._cloud_q.append(queued)

        self.assertIsNone(scheduler._next_cloud_job())
        self.assertEqual("queued", queued.status)

    async def test_demo_scenario_has_expected_deterministic_placements(self) -> None:
        placements = []
        for text in DEMO_BATCH:
            verdict, _ = judge.classify(text, force_fallback=True)
            placements.append(policy.allocate(verdict)["route"])

        self.assertEqual(
            ["cloud", "cloud", "cloud", "local", "local", "local", "local"],
            placements,
        )

    async def test_snapshot_preserves_scenario_submission_order(self) -> None:
        scheduler = Scheduler(AsyncMock())
        for text in DEMO_BATCH:
            job = Job(text)
            scheduler.jobs[job.id] = job

        self.assertEqual(DEMO_BATCH, [job["text"] for job in scheduler.snapshot()["jobs"]])


if __name__ == "__main__":
    unittest.main()