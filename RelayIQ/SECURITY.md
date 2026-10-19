---
title: Use AI in the Right Place security policy
description: Responsible vulnerability reporting and prototype security boundaries.
ms.date: 2026-08-13
ms.topic: reference
---

## Report a vulnerability

Do not open a public issue for a vulnerability that could expose credentials or
personal information. Contact the repository maintainers privately and include
the affected component, reproduction steps, impact, and suggested mitigation.

## Supported version

Only the latest revision on the default branch is supported during the prototype phase.

## Security boundaries

Use AI in the Right Place uses conservative pattern-based sanitization for demonstration. It is not
a replacement for enterprise data-loss prevention, clinical safety controls, or
professional emergency response. Use synthetic identifiers in demonstrations.

The Pi integration keeps the persistent agent session and all tool access local.
Its isolated Cloud Advisor receives only the RelayIQ `permitted_payload`. Do not
modify the extension to forward Pi history, system prompts, tool schemas,
tool results, files, or images without a new structured egress review. Bind the
prototype RelayIQ API to loopback; remote deployments require HTTPS and an
authentication layer outside this prototype.