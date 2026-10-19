---
title: Use AI in the Right Place policy catalog
description: Placement policies, precedence, signals, actions, and decision-record behavior.
author: Use AI in the Right Place contributors
ms.date: 2026-08-13
ms.topic: reference
keywords:
  - policies
  - intelligence placement
  - privacy
estimated_reading_time: 4
---

## Policy contract

Use AI in the Right Place evaluates five signals before allocating intelligence:

* Urgency: critical, general, or casual
* Reasoning depth: high or low
* Fresh knowledge requirement
* Sensitive-data detection
* Network availability and cloud data budget

Every decision returns a placement, policy name, headline, reason, action,
factors, and estimated cloud cost. Explanations never include detected secrets.

## Policy precedence

1. Privacy Guard v2 sanitizes cloud-bound sensitive requests and requires consent.
2. Resilient Fallback v1 provides local help when the selected cloud lane is unavailable.
3. Fresh Knowledge v1 selects cloud capability for time-dependent answers.
4. Deep Reasoning v1 selects cloud capability for extended analysis.
5. Local First v1 keeps stable, low-reasoning requests on device.

## Privacy Guard v2

* Trigger: sensitive markers in a cloud-bound request
* Placement: pending approval or hybrid
* Action: sanitize locally, preview the payload, require human consent
* Emergency behavior: provide immediate local guidance while consent remains pending

Urgency never authorizes disclosure. Approved cloud calls use only the sanitized
payload; rejection keeps the request local.

## Resilient Fallback v1

* Trigger: a cloud-selected request while connectivity is unavailable
* Placement: local now, cloud later
* Action: return best-effort local guidance and retain prioritized refinement

## Fresh Knowledge v1

* Trigger: correctness depends on current information
* Placement: cloud, subject to connectivity and budget
* Action: queue by urgency and reasoning priority, then require grounded web search

## Deep Reasoning v1

* Trigger: extended reasoning exceeds the local-first capability policy
* Placement: cloud, subject to connectivity and budget
* Action: queue by urgency and reasoning priority

## Local First v1

* Trigger: low reasoning depth with no fresh-information requirement
* Placement: local
* Action: process entirely on device and avoid a cloud call

## Prototype boundary

Policies are implemented in Python for this prototype. Production evolution
should externalize signed, versioned policy bundles with schema validation,
organization-specific overrides, confidence calibration, and audit export.