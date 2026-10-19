---
title: Use AI in the Right Place executive demo
description: Record-ready two-minute walkthrough for policy-driven AI request placement.
author: Use AI in the Right Place contributors
ms.date: 2026-08-13
ms.topic: how-to
keywords:
  - hackathon demo
  - on-device AI
  - zero-cost AI
  - Foundry Local
estimated_reading_time: 6
---

## Demo promise

> Use AI in the Right Place gives every request the least expensive intelligence that can handle
> it, keeps working when the network cannot, and shows every decision live.

The executive story is not model routing. It is an isolated placement decision
that any application can call before execution, under real constraints:
capability, privacy, connectivity, consent, and budget.

Keep sustainability as a supporting metric, not the challenge headline. Lead
with zero-cost, on-device productivity and visible operational control.

## Two-minute run of show

| Time | Screen | Action and point |
|---|---|---|
| 0:00-0:15 | README opening | Deliver the hook, category, and differentiator |
| 0:15-0:22 | High-level architecture | Show the reusable placement boundary |
| 0:22-0:30 | Detailed architecture | Show the working Foundry implementation |
| 0:30-0:43 | UI: Algarve | Submit the stable request and point to Local First |
| 0:43-0:56 | UI: FIFA | Submit the fresh request and point to cloud placement |
| 0:56-1:08 | UI: disconnected FIFA | Toggle offline and point to local-now, cloud-later fallback |
| 1:08-1:27 | UI: full scenario | Select Run scenario and scan priority, consent, and placements |
| 1:27-1:37 | UI: savings | Point to calls avoided, bandwidth, local answers, and score |
| 1:37-1:49 | API and OpenAPI | Show decision-only integration contract |
| 1:49-2:00 | Performance scorecard | Flash quality, critical recall, overhead, and the deployment caveat |

## Prepare the recording

Run these commands before opening the recorder:

```powershell
python -m unittest discover -s tests -v
foundry model load phi-4-mini
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open the following tabs in this order:

1. The published GitHub repository at the README opening
2. The README architecture section in a separate tab
3. The [performance scorecard](../assets/performance-scorecard.png) in a
  full-screen tab
4. The Use AI in the Right Place console at <http://127.0.0.1:8000>
5. OpenAPI at <http://127.0.0.1:8000/docs>

Set the browser zoom so the input, resource controls, first decision, and
Resource Efficiency panel fit on screen. In the console:

1. Select Reset
2. Set connectivity to on
3. Set the cloud data budget to 40 KB
4. Confirm the header names `phi-4-mini` as the local model
5. Confirm whether the cloud lane says real cloud or local simulation

Local cloud simulation is valid for the zero-cost demo. Name it clearly. Do not
describe a simulated response as an Azure cloud call.

## Two-minute narration

### 0:00-0:15 Positioning

Show the README title and first diagram.

Say:

> Every AI request has a best place to run, but most applications default to
> cloud. Use AI in the Right Place is a policy-driven placement layer that
> decides before execution. Unlike a model router, it combines intent, privacy,
> consent, connectivity, and budget. The application stays in control.

### 0:15-0:30 Architecture

Show the high-level diagram, then immediately scroll to the detailed diagram.

Say:

> At the top level, applications, copilots, and agents call one decision API,
> then execute locally, in cloud, or through consent. The working Foundry proof
> implements the local Judge, policy, privacy, scheduling, and fallback path.

### 0:30-0:56 Local and cloud placement

Submit the Algarve prompt:

```text
What's the average sea temperature in Algarve, Portugal in August?
```

Then submit the FIFA prompt:

```text
What country won FIFA 2026 in July?
```

Say:

> Algarve is stable knowledge, so Local First keeps it private, offline-ready,
> and free of a cloud call. FIFA depends on fresh information, so Fresh
> Knowledge selects cloud capability. Same application, different placement.

### 0:56-1:08 Disconnected fallback

Select Reset, toggle connectivity off, and submit the FIFA prompt again.

Say:

> Now the same fresh request arrives without connectivity. The service answers
> locally now and retains it for cloud refinement after reconnect.

### 1:08-1:37 Full scenario and savings

Select Reset, restore connectivity, and select **Run scenario**. Use a completed
or pre-warmed run so there is no recording delay. Scan the decisions, approve
the sanitized emergency payload, then point to the Resource Efficiency panel.

Say:

> The mixed scenario places urgent, deep, routine, and sensitive work together.
> Critical work receives priority, sensitive cloud payloads pause for consent,
> and routine work stays local. The panel shows calls avoided, bandwidth saved,
> local answers, and efficiency. Routing and bytes are measured; currency and
> carbon are estimates.

### 1:37-1:49 Isolated placement API

Show `/docs` with `POST /v1/placement/decide`, or show the README request and
response side by side.

Say:

> The UI is only one caller. The REST contract returns placement, policy,
> permitted payload, consent, fallback, and timings without executing an answer
> model. Applications and agents can integrate the decision directly.

### 1:49-2:00 Evidence and close

Hard cut to the full-screen performance scorecard. Hold it for the complete
closing line without scrolling.

Say:

> Across 200 mixed cases, urgency accuracy is 96.5 percent with zero critical
> misses. Placement logic is sub-millisecond, and the REST boundary adds about
> three milliseconds in a warmed loopback check. Model inference and execution
> depend on the chosen deployment. Use AI in the right place, under policy.

## Recording choreography

* Preload all README anchors and OpenAPI before recording
* Keep a completed full-scenario run in a second browser tab as a timing backup
* Preload the performance scorecard at fit-to-window zoom for the final cut
* Use browser zoom between 80% and 90% so decisions and savings remain visible
* Do not wait for generated answer prose; placement is the product evidence
* Use hard cuts between architecture, UI, API, and the performance scorecard
* Show the simulated-cloud label whenever Azure cloud is not configured

## Extended rehearsal notes

The following material explains each scene in more depth. Use it to rehearse
answers, not as the primary two-minute narration.

### Opening problem

Show the first viewport of the README.

Say:

> Most AI sends every request to the cloud. That assumes always-on internet,
> unlimited cloud compute, one device per person, and no privacy constraints.
> That is expensive, fragile, and unnecessary for routine work.
>
> Intelligence is becoming abundant. Energy and connectivity are not. AI
> should optimize for the scarce resource, not only the smartest model.

Scroll to the Use AI in the Right Place heading and pause on the product proposition.

Say:

> Use AI in the Right Place is a policy-driven request-placement layer. Its
> proposition is simple: keep work local by default and use cloud intelligence
> when policy and user needs justify it.
>
> Use AI in the Right Place gives every request the least expensive intelligence that can handle
> it. A policy-driven relay evaluates urgency, reasoning depth, freshness,
> privacy, connectivity, and budget before placing work on a local model, a
> cloud model, or both. Routine productivity stays on device at zero cloud
> cost; specialized capability is used only when the request and policy
> require it.
>
> The application retains control of execution. This repository
> contains the working prototype, evaluation data, tests, and setup
> instructions, not a presentation mockup.

### Architecture walkthrough

Show the Mermaid diagram in `docs/architecture.md`.

Say:

> The user sends a request through the web console and FastAPI WebSocket. A
> small `phi-4-mini` Judge running on device with Foundry Local classifies its
> urgency, reasoning depth, freshness, category, and priority. The policy
> engine turns those signals into a local or cloud-eligible placement.
>
> Before any possible cloud send, the privacy gate scans locally for sensitive
> markers. It redacts detected values and requires human approval for a
> cloud-bound request. Stable work is answered by `phi-4-mini` on device.
> Cloud-eligible work enters the scheduler, which first checks connectivity and
> remaining budget. When multiple requests are waiting, critical work is served
> before general work. The real cloud lane uses the configured Microsoft
> Foundry deployment; a zero-cost demo can simulate that lane with a second
> Foundry Local call.
>
> If the network is unavailable, `phi-4-mini` gives a best-effort answer now and
> the scheduler retains eligible work for refinement after reconnection. Every
> path emits the same decision record: placement, policy, reason, action,
> factors, and estimated cloud cost.

### Working repository tour

Switch briefly to VS Code with the `app/` directory expanded. Point to each
file without scrolling through implementation details.

Say:

> The architecture maps directly to the repository. `main.py` hosts the console
> and WebSocket commands. `judge.py` produces the request signals. `policy.py`
> selects the versioned placement policy. `privacy.py` scans and redacts before
> cloud egress. `scheduler.py` owns connectivity, cloud budget, queue order,
> approval, and fallback. `foundry.py` contains the Foundry Local and GPT cloud
> adapters. The behavior is exercised by the evaluation suites and regression
> tests here in the same repository.

Keep this tour to 15 seconds. Show file names and boundaries, not code details.

### Transition to the live system

Switch to the Use AI in the Right Place console.

Say:

> That is the proposition and the architecture. Now let's see how the service
> allocates intelligence request by request, starting with the simplest case.

### Isolated request 1: local and zero cloud cost

Open the console and submit:

```text
What's the average sea temperature in Algarve, Portugal in August?
```

Wait for the response. Point to the Local badge, Local First v1, and the action
line. Then point to Cloud calls avoided and Local answers.

Say:

> This asks for a typical seasonal average, not today's measured sea
> temperature. It does not need current information or extended reasoning, so
> Local First keeps the request on device. The user gets a useful answer with
> no cloud call and no network dependency.

### Isolated request 2: capability when it matters

Submit:

```text
What country won FIFA 2026 in July?
```

Point to the Cloud badge, Fresh Knowledge v1, the model name in the decision,
Web search required under policy factors, and the estimated cloud cost.

Say:

> This event happened after the local model's training cutoff, so the Judge
> marks it as fresh knowledge. The service sends it to the configured cloud
> deployment and requires grounded web search. The answer cites an
> official FIFA source.

If the header says simulated cloud, say:

> For this zero-cost run, the cloud lane is simulated locally. The queue,
> budget, byte accounting, and policy behavior are unchanged; production can
> connect the same lane to an Azure OpenAI deployment.

Web search applies only to a real Azure cloud lane. Local cloud simulation
demonstrates freshness-based placement but cannot return a web-grounded answer.

### Isolated request 3: connectivity off

Select Reset, turn connectivity off, and submit the same request:

```text
What country won FIFA 2026 in July?
```

Point to Offline adaptation in the header, Resilient Fallback v1, Local now,
cloud later, and the local response.

Say:

> The policy still knows this request needs fresh knowledge, but the cloud and
> web search are unavailable. The service does not leave the user waiting. It provides a
> best-effort answer on device, clearly limited by the local model's training
> cutoff, and retains the request for refinement after reconnection.

Turn connectivity on and pause while the request moves through the cloud lane.

Say:

> When connectivity returns, queued work resumes according to priority and the
> remaining data budget.

### Full policy scenario

Select Reset, confirm connectivity is on, and select Run scenario. Allow the
requests to finish before scrolling.

Say:

> Now imagine many requests arriving where compute, connectivity, and budget
> are scarce. Every request first passes through the `phi-4-mini` Judge running
> on device. It classifies urgency, reasoning depth, and freshness. The policy
> engine uses those signals to decide placement and queue priority.

Point first to the collapsed-colleague request and its approval controls.

Say:

> The most urgent request appears first. The service immediately exposes the
> sanitized approval decision while local emergency guidance is generated in
> parallel. Waiting for a person does not occupy or block the cloud queue.

Approve the sanitized cloud send. Then point to the diarrhea request and the
ibuprofen request.

Say:

> Approval adds the emergency to the cloud queue at emergency priority. A cloud
> call already in progress is never interrupted, but the approved emergency
> receives the next available slot ahead of lower-priority work. While approval
> was pending, the diarrhea and ibuprofen requests remained eligible to move.
> The diarrhea request is served before the ibuprofen question because its need
> for immediate current guidance gives it the higher Judge score.

Point to the quadratic-equation, maize-storage, employee-note, and joke
requests in that order.

Say:

> These routine requests are dispatched to the local answer lane independently
> of cloud work. They stay visible in a stable narrative order: equation, maize,
> employee note, and joke. The employee note contains an identifier, but the
> policy keeps it local, so no approval is required because nothing leaves the
> device. Sending any of these requests to the cloud would add cost and
> dependency without enough additional value.

### Measurable outcomes

Return to the Resource Efficiency panel. Switch the currency between USD and
EUR, then expand How this score is calculated.

Say:

> Use AI in the Right Place turns routing decisions into operational measures. It counts local
> answers and cloud calls avoided, measures payload bytes kept off the network,
> and reports the resulting Resource Efficiency Score.
>
> Payload bytes and routing outcomes are measured by this run. USD, EUR, and
> carbon values are modeled estimates based on visible, configurable
> coefficients. They are directional planning signals, not billing or emissions
> telemetry.

Read the actual values visible in the recording. Do not memorize values because
model response length changes them between runs.

### Close

End on the full console with the metrics and policy trace visible.

Say:

> The future of enterprise AI is not one expensive model answering every
> request. It is the right intelligence, in the right place, under explicit
> policy. Use AI in the Right Place makes on-device AI useful now, cloud AI selective, and every
> decision accountable.

## Optional extended edit plan

For a longer version, record three clean clips instead of one continuous take:

1. GitHub README and architecture, about 75 seconds
2. Three isolated UI requests, about 130 seconds
3. Full scenario, metrics, and close, about 95 seconds

Use 1080p landscape capture. Keep the pointer still while speaking, then move
it to the exact badge or metric being discussed. Use hard cuts between clips
and brief crossfades only when changing from GitHub to the live console.

Suggested title cards:

* Opening: `Use AI in the Right Place`
* Architecture: `Judge locally. Allocate by policy.`
* Offline transition: `No network. No interruption.`
* Metrics: `Measured decisions. Visible outcomes.`
* Closing: `The right intelligence, in the right place.`

Avoid background music during narration. Add captions and verify that product
names, model names, and metric units are transcribed correctly.

## Recovery plan

If a model response takes too long, keep narrating the visible policy decision.
The decision is the product; answer prose is supporting evidence.

If the local endpoint is unavailable:

```powershell
foundry server start
foundry model load phi-4-mini
```

If the real cloud lane fails, remove cloud credentials and restart the app to
use clearly labeled local simulation. Never troubleshoot credentials during a
recording.

If a prompt receives an unexpected classification, Reset and use the fallback
prompt documented for that scene.

## Evidence after the demo

Run these checks before publishing the final recording:

```powershell
python -m compileall -q app eval tests
python -m unittest discover -s tests -v
python eval/evaluate_judge.py --suite challenge
```

The latest mixed benchmark reports 96.50% urgency accuracy, zero critical misses
across 66 critical cases, 74.00% route accuracy, and 100% decision completion.
Deterministic placement remained under 0.3 ms p95 across 200 cases in repeated
recorded runs, and the REST boundary added 3.2 ms mean overhead across five
warmed loopback requests. These figures exclude model inference and answer
execution. Independent quality review and deployed availability testing remain
required.
