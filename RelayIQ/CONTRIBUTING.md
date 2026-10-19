---
title: Contributing to Use AI in the Right Place
description: Local setup, validation, and pull-request guidance for contributors.
ms.date: 2026-08-13
ms.topic: how-to
---

## Development setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Do not commit `.env`, credentials, model files, or user prompts containing real
personal information.

## Required checks

```powershell
python -m compileall -q app eval tests
python -m unittest discover -s tests -v
python eval/evaluate_judge.py --suite challenge
```

Policy changes must include tests for the selected placement and explanation.
Privacy changes must prove that both labels and identifier values are removed.

## Pull requests

Keep changes focused, explain policy behavior changes, report validation results,
and disclose whether impact values are measured or modeled.