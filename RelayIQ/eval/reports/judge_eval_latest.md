---
title: Judge evaluation report
description: Latest deterministic fallback evaluation and safety-critical misses.
ms.date: 2026-08-13
ms.topic: reference
---

## Run Summary

* Cases: 200
* Dataset: eval/data/judge_challenge.jsonl
* Suite: challenge
* Interpretation: Mixed candidate-label benchmark: 36 hand-authored adversarial seeds plus 164 seeded matrix cases; independent review pending
* Mode: fallback-heuristic
* Decision success rate: 100.00% (200/200)
* Fallback decision rate: 100.00% (200/200)

This report evaluates deterministic fallback behavior. It does not measure
Foundry Local Judge model quality or model inference latency.

## Topline Accuracy

* urgency: 96.50%
* complexity: 53.50%
* needs_fresh: 86.00%
* route: 74.00%
* tier: 53.50%
* critical urgency miss rate: 0.00% (0/66)
* unnecessary cloud rate: 90.57% (48/53)
* missed cloud rate: 2.72% (4/147)

## Precision, Recall, and F1

* urgency: macro precision 96.80%, macro recall 96.36%, macro F1 96.48%
* complexity: macro precision 61.79%, macro recall 53.07%, macro F1 42.65%
* needs_fresh: macro precision 87.15%, macro recall 86.55%, macro F1 85.98%
* route: macro precision 65.22%, macro recall 53.35%, macro F1 50.37%
* tier: macro precision 43.86%, macro recall 52.24%, macro F1 40.02%

## Placement Latency

* mean: 0.119 ms
* p50: 0.118 ms
* p95: 0.214 ms
* max: 0.299 ms

## Exact Matrix Conformance

Each slice requires urgency, complexity, freshness, route, and tier to all
match the candidate labels. A zero here is not a critical-urgency miss.

* casual|high|False: 62.50%
* casual|high|True: 93.33%
* casual|low|False: 17.65%
* casual|low|True: 0.00%
* critical|high|False: 83.33%
* critical|high|True: 100.00%
* critical|low|False: 0.00%
* critical|low|True: 0.00%
* general|high|False: 84.21%
* general|high|True: 82.35%
* general|low|False: 10.53%
* general|low|True: 6.25%

## Urgency Confusion (expected->predicted)

* casual->casual: 57
* casual->general: 6
* critical->critical: 66
* general->casual: 1
* general->general: 70
