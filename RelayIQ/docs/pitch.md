---
title: Use AI in the Right Place hackathon pitch
description: Submission-ready positioning for policy-driven AI request placement.
author: Use AI in the Right Place contributors
ms.date: 2026-08-13
ms.topic: overview
keywords:
  - hackathon pitch
  - on-device AI
  - request placement
  - intelligent routing
estimated_reading_time: 4
---

## Tagline

**Use AI in the Right Place.**

## One-sentence pitch

Use AI in the Right Place is a policy-driven placement layer that applications,
copilots, and agents can call before executing AI work, keeping routine work
local and using cloud intelligence only when policy and user needs justify it.

## Submission description

Most AI products bind placement to one application or send every request to the
cloud. Use AI in the Right Place makes placement a reusable, inspectable
decision before execution.

A small Judge running with Foundry Local classifies each request for urgency,
reasoning depth, and freshness. Versioned policies combine those signals with
privacy, connectivity, and a cloud data budget to select the right execution
path. Routine work stays with `phi-4-mini` on device. Fresh or extended work can
use an Azure OpenAI deployment. Sensitive prompts are sanitized locally and
paused for human consent. During an outage, the service answers locally and
resumes eligible refinement after reconnection.

The REST API returns placement, policy, permitted payload, consent, fallback,
model suggestions, reason codes, and an explanation without executing an answer
model. The included policy console then proves the same decisions end to end
with Foundry Local and a Microsoft Foundry cloud deployment or local cloud
simulation.

## Challenge fit

Use AI in the Right Place directly addresses the on-device productivity challenge:

* Small models perform useful information-worker tasks entirely on device
* Routine requests avoid cloud round trips and cloud inference cost
* Local execution remains available without network access
* Sensitive identifiers can remain on the device
* Cloud capability remains available for work that truly needs it
* Live policy traces make the system understandable to operators and users

The primary challenge story is reusable AI placement. On-device productivity,
privacy, resilience, and resource efficiency are measurable outcomes.

## Problem and solution

Most AI assumes always-on connectivity, unconstrained cloud capacity, and
permission to transmit every prompt. That creates avoidable cost, latency,
privacy exposure, and failure during outages.

The placement layer evaluates six constraints before execution:

1. Urgency
2. Reasoning depth
3. Fresh knowledge
4. Privacy
5. Connectivity
6. Cloud data budget

Model selection is the output of that policy decision, not the product itself.

## Differentiation

A routing conditional picks a model. Use AI in the Right Place allocates
intelligence and emits an inspectable decision record for every request.

The prototype combines:

* A local Judge for request classification
* Versioned placement policies with human-readable explanations
* Local sensitive-data sanitization and explicit consent
* Priority scheduling within a configurable cloud data budget
* Immediate offline fallback and reconnect refinement
* A live Resource Efficiency panel for operational outcomes

## Measurable outcomes

The console reports these values for each run:

| Outcome                    | Basis                                     |
|----------------------------|-------------------------------------------|
| Local answers              | Measured completed local requests         |
| Cloud calls avoided        | Measured local share of resolved requests |
| Payload saved              | Measured UTF-8 request and response bytes |
| Bandwidth saved            | Measured against an all-cloud baseline    |
| Resource Efficiency Score  | Composite of visible measured percentages |
| Estimated value in USD/EUR | Modeled with configurable coefficients    |
| Estimated CO2 avoided      | Modeled with configurable coefficients    |

The 200-case mixed fallback benchmark reports 96.50% urgency accuracy, 96.48%
macro urgency F1, zero critical misses across 66 critical cases, 74.00% route
accuracy, 50.37% macro route F1, and 100% decision success. Deterministic
fallback placement remained under 0.3 ms p95 in repeated recorded runs, and the
REST boundary added approximately 3.2 ms mean overhead across five warmed
loopback requests. These placement-layer figures exclude model inference and
answer execution. The benchmark combines 36 hand-authored adversarial seeds
with 164 seeded matrix cases. Candidate labels require independent review,
route quality still needs improvement, and decision success is not
service-level availability.

## Demo story

1. A routine productivity request stays on device and avoids a cloud call.
2. A freshness-dependent request receives cloud eligibility and an explanation.
3. Connectivity turns off; Resilient Fallback answers locally without delay.
4. Connectivity returns; priority scheduling resumes within the data budget.
5. A synthetic sensitive prompt is sanitized and waits for human consent.
6. The full scenario converts policy choices into measured outcomes.

Use the exact prompts and narration in [DEMO.md](DEMO.md).

## Closing

The future of enterprise AI is not one model answering every request. It is
AI in the right place, under explicit policy.