# RelayIQ v2 placement API

The standalone service implements the Placement API v2 Draft 4 boundary
at `POST /v2/placement/decide`. It recommends a model class and requirements;
it does not invoke an answer model, run tools, manage provider credentials, or
return a sanitized payload.

For a scenario-by-scenario checklist with PowerShell commands, expected results,
and isolated error fixtures, see the [contract validation guide](placement-v2-validation.md).

## Configuration

`RELAYIQ_POLICY_CONFIG` must point to trusted, non-secret operator
configuration. The supplied POC snapshot is `config\relayiq-policy.json`:

- revision `relayiq-judge-v1`
- local and cloud permitted
- local pre-inference failure handling permitted
- exact configured deny input `Demonstrate the configured demo block.`

Draft 4 removes redaction-profile negotiation. Existing Draft 3 policy files
containing `supported_redaction_profiles` must be migrated; the strict loader
rejects them. Requests containing `context.redaction_profiles` return HTTP 400.
The caller performs redaction using the Judge's category findings as guidance;
RelayIQ does not sanitize or verify a cloud payload.

An invalid configured file fails service startup visibly. If the setting is
absent, a valid placement request returns HTTP 500 `INTERNAL_ERROR` before the
Judge is called. Policy evaluation failures also return HTTP 500, regardless of
local fallback permissions. The API never returns `DECISION_UNAVAILABLE` and
does not load permissive defaults. Every successful decision has a non-blank
`policy_revision`. An explicit policy denial (including no permitted targets)
is different: it returns HTTP 200 with `POLICY_BLOCKED` and `fallback`/`stop`.

## Start Foundry Local and the v2 API

Complete the root README's Python setup first. From the repository root in
PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1

foundry server start
foundry model load phi-4-mini

$env:RELAYIQ_POLICY_CONFIG = (Resolve-Path .\config\relayiq-policy.json)
python -m uvicorn app.api:app --host 127.0.0.1 --port 8891
```

Keep that terminal open. In another PowerShell window, verify the server:

Endpoint discovery uses the current `foundry server status` command and UTF-8
CLI output on Windows, with older CLI fallbacks retained. If discovery fails,
set `FOUNDRY_ENDPOINT` to the Foundry service URL shown by `foundry server status`
before starting RelayIQ; do not use RelayIQ's own port `8891`. A discovery
failure produces `CLASSIFIER_DEGRADED`, not a healthy negative email finding.
The fallback's broader privacy checks can still require cloud consent.

V2 Judge inference uses `JUDGE_TIMEOUT_SECONDS` (default `30`, finite and
positive) with SDK retries disabled. A timed-out model call produces
`CLASSIFIER_DEGRADED`; this bounds waiting rather than making inference faster.
It is an inference inactivity timeout, not a total request deadline: endpoint
discovery and loading a missing model happen separately. Preload the model for
human validation. Legacy Judge and answer-model calls keep their existing
client options. On a VM without hardware GPU acceleration, choose an appropriate
CPU variant explicitly rather than relying on a GPU-oriented alias.

The v2 model prompt requests six strict booleans: the four non-sensitivity
classification flags, `email_detected`, and `requires_consent`. The adapter maps
`email_detected` to public `classification.sensitive_data` and the single
`email_address` category. It derives redundant internal routing fields from the
same model signals, rather than asking the LLM to repeat them. Missing or invalid
signals still degrade; no false defaults are synthesized. The output budget is
160 tokens. The public response shape and legacy v1 Judge mode are unchanged.

For a VM where the GPU variant is slow, an explicit CPU setup is:

```powershell
foundry model download Phi-4-mini-instruct-generic-cpu:5
foundry model load Phi-4-mini-instruct-generic-cpu:5
$env:JUDGE_MODEL = "Phi-4-mini-instruct-generic-cpu"
$env:JUDGE_TIMEOUT_SECONDS = "30"
# Restart the v2 API after setting these variables.
```

Use the API model ID without the CLI variant revision suffix for `JUDGE_MODEL`.
If multiple variants are loaded, pin the intended model rather than an ambiguous
alias. Hardware and warm-up affect latency; a faster model is not a suitable
replacement unless its classification accuracy is also checked.

```powershell
Invoke-WebRequest http://127.0.0.1:8891/docs -UseBasicParsing |
  Select-Object StatusCode
```

The expected status is `200`. Interactive OpenAPI documentation is at
<http://127.0.0.1:8891/docs>. Stop only the Uvicorn process with `Ctrl+C`.

The legacy UI and `POST /v1/placement/decide` remain on `app.main:app` with
their existing scheduler-derived defaults and v1 response shape. V2 uses fixed
defaults (`connectivity=true`, unbounded request budget, and an empty capability
list), preserves omitted versus supplied-empty model mappings,
and measures budget as original UTF-8 input bytes divided by 1024.

When `model_mapping` is omitted, `suggested_models` is omitted entirely for
every target, including fallback. With a supplied mapping it is present; an
empty mapping makes both lanes ineligible and returns fallback with
`{"local":[],"cloud":[]}`. Normal recommendations return both request lists,
in order, as `{"local":[...],"cloud":[...]}` even when only one lane is
recommended. Missing request lanes become empty arrays. These lists describe
inventory, not authorization: select from the recommended lane and respect
consent, policy, connectivity, budget, and fallback requirements.

## Invoke the API

Define this helper once in the PowerShell window used for requests:

```powershell
function Invoke-RelayIQPlacement {
  param([hashtable]$Request)

  Invoke-RestMethod `
    -Method Post `
    -Uri http://127.0.0.1:8891/v2/placement/decide `
    -ContentType "application/json" `
    -Body ($Request | ConvertTo-Json -Depth 5) |
    ConvertTo-Json -Depth 5
}
```

Each `decision_id` is a newly generated UUID. Examples illustrate normal-path
Judge assessments; actual flags and category labels depend on the model's
assessment. Healthy decisions include all five boolean `classification` fields.
When `classification.sensitive_data` is true, `SENSITIVE_DATA_DETECTED` and a
non-empty `sensitive_data_types` array accompany it; otherwise both are omitted.
The category vocabulary is extensible and contains labels, never detected values.

The current `relayiq-judge-v1` Judge has an email-only sensitivity scope. It uses
LLM judgment to detect actual email addresses and emits exactly
`["email_address"]` for positive findings, even for multiple addresses. Names,
SSNs, employee IDs, phone numbers, financial data, and confidential text without
an email address do not independently trigger `SENSITIVE_DATA_DETECTED`.
Mentioning email without an address also does not qualify. Other positive
categories from the model are out of scope and degrade the assessment rather
than publishing an unsupported finding. The API vocabulary remains extensible
for future Judge versions; caller parsers must not hardcode this current scope.
Consent remains independent and may be required for non-email information.
Retain broader caller redaction protections; a negative email finding is not
proof of safe disclosure. Restart the API after policy or Judge changes.

If Foundry Local cannot classify a request or returns an invalid Draft 4
assessment, the API returns a bounded decision with `CLASSIFIER_DEGRADED` and
omits `classification`, `SENSITIVE_DATA_DETECTED`, and `sensitive_data_types`.
Omission does not guarantee safety. Restore Foundry Local before treating that
result as normal-path validation. The legacy Judge interface is unchanged;
v2 opts into the expanded output structure.

### Local recommendation

```powershell
$request = @{
  input = "What is 12 + 15? Reply with only the number."
  preferred_target_class = "local"
  model_mapping = @{
    local = @("phi-4-mini")
    cloud = @("nvidia/nemotron-3.5-lightning-30b-a3b")
  }
  context = @{
    connectivity = $true
    cloud_budget_kb = $null
    required_capabilities = @()
  }
}

Invoke-RelayIQPlacement $request
```

Expected response:

```json
{
  "decision_id": "<generated UUID>",
  "target_class": "local",
  "policy_revision": "relayiq-judge-v1",
  "requirements": {
    "consent_required": false
  },
  "fallback_action": "local",
  "reason_codes": ["PREFERENCE_HONORED"],
  "classification": {
    "routine": true,
    "fresh_knowledge": false,
    "reasoning_heavy": false,
    "critical": false,
    "sensitive_data": false
  },
  "explanation": "The local class is permitted and eligible.",
  "suggested_models": {
    "local": ["phi-4-mini"],
    "cloud": ["nvidia/nemotron-3.5-lightning-30b-a3b"]
  }
}
```

### Cloud recommendation without consent

The Judge determines consent per request from the prompt and supplied context.
This non-sensitive request can be recommended for cloud execution without
consent.

```powershell
$request = @{
  input = "Compare two architecture options for this service."
  preferred_target_class = "cloud"
  model_mapping = @{
    local = @("phi-4-mini")
    cloud = @("nvidia/nemotron-3.5-lightning-30b-a3b")
  }
  context = @{
    connectivity = $true
    cloud_budget_kb = $null
    required_capabilities = @("extended_reasoning")
  }
}

Invoke-RelayIQPlacement $request
```

Expected response:

```json
{
  "decision_id": "<generated UUID>",
  "target_class": "cloud",
  "policy_revision": "relayiq-judge-v1",
  "requirements": {
    "consent_required": false
  },
  "fallback_action": "local",
  "reason_codes": ["PREFERENCE_HONORED"],
  "classification": {
    "routine": false,
    "fresh_knowledge": false,
    "reasoning_heavy": true,
    "critical": false,
    "sensitive_data": false
  },
  "explanation": "The cloud class is permitted and eligible.",
  "suggested_models": {
    "local": ["phi-4-mini"],
    "cloud": ["nvidia/nemotron-3.5-lightning-30b-a3b"]
  }
}
```

The caller must handle the data safely before invoking the selected answer
model. Consent is not required for this recommendation, but a negative
sensitivity assessment is not proof of safety. The placement response does not
make a cloud call.

### Cloud recommendation with consent

This request contains an email address and confidential employee information,
so the Judge can report email detection and independently return the distinct
`cloud_with_consent` recommendation:

```powershell
$request = @{
  input = "Review employee id EMP-20431, email alex.example@example.com, and salary."
  preferred_target_class = "cloud"
  model_mapping = @{
    local = @("phi-4-mini")
    cloud = @("nvidia/nemotron-3.5-lightning-30b-a3b")
  }
  context = @{
    connectivity = $true
    cloud_budget_kb = $null
    required_capabilities = @("extended_reasoning")
  }
}

Invoke-RelayIQPlacement $request
```

Expected response:

```json
{
  "decision_id": "<generated UUID>",
  "target_class": "cloud_with_consent",
  "policy_revision": "relayiq-judge-v1",
  "requirements": {
    "consent_required": true
  },
  "fallback_action": "local",
  "reason_codes": [
    "PREFERENCE_HONORED",
    "SENSITIVE_DATA_DETECTED"
  ],
  "classification": {
    "routine": false,
    "fresh_knowledge": false,
    "reasoning_heavy": true,
    "critical": false,
    "sensitive_data": true
  },
  "sensitive_data_types": ["email_address"],
  "explanation": "The cloud class is permitted and eligible; the judge requires caller consent.",
  "suggested_models": {
    "local": ["phi-4-mini"],
    "cloud": ["nvidia/nemotron-3.5-lightning-30b-a3b"]
  }
}
```

The caller must redact sensitive data before either cloud outcome, using the
reported categories as guidance. Only `cloud_with_consent` requires the caller
to obtain approval before invoking the selected model; consent is not proof of
redaction. Sensitivity findings do not override placement or consent rules.

Removing the email address from this request should produce a healthy
`classification.sensitive_data: false` assessment with neither
`SENSITIVE_DATA_DETECTED` nor `sensitive_data_types`. Consent can still be
required because the request contains confidential employee information.

### Local fallback while offline

```powershell
$request = @{
  input = "Compare two architecture options for this service."
  preferred_target_class = "cloud"
  model_mapping = @{
    local = @("phi-4-mini")
    cloud = @("nvidia/nemotron-3.5-lightning-30b-a3b")
  }
  context = @{
    connectivity = $false
    cloud_budget_kb = $null
    required_capabilities = @("extended_reasoning")
  }
}

Invoke-RelayIQPlacement $request
```

Expected response:

```json
{
  "decision_id": "<generated UUID>",
  "target_class": "local",
  "policy_revision": "relayiq-judge-v1",
  "requirements": {
    "consent_required": false
  },
  "fallback_action": "local",
  "reason_codes": [
    "CONNECTIVITY_UNAVAILABLE",
    "PREFERENCE_OVERRIDDEN"
  ],
  "classification": {
    "routine": false,
    "fresh_knowledge": false,
    "reasoning_heavy": true,
    "critical": false,
    "sensitive_data": false
  },
  "explanation": "The cloud class is unavailable under CONNECTIVITY_UNAVAILABLE; the eligible local class is recommended.",
  "suggested_models": {
    "local": ["phi-4-mini"],
    "cloud": ["nvidia/nemotron-3.5-lightning-30b-a3b"]
  }
}
```

### Policy-blocked request

```powershell
$request = @{
  input = "Demonstrate the configured demo block."
  model_mapping = @{
    local = @("phi-4-mini")
    cloud = @("nvidia/nemotron-3.5-lightning-30b-a3b")
  }
  context = @{
    connectivity = $true
    cloud_budget_kb = $null
    required_capabilities = @()
  }
}

Invoke-RelayIQPlacement $request
```

Expected response:

```json
{
  "decision_id": "<generated UUID>",
  "target_class": "fallback",
  "policy_revision": "relayiq-judge-v1",
  "requirements": {
    "consent_required": false
  },
  "fallback_action": "stop",
  "reason_codes": ["POLICY_BLOCKED"],
  "classification": {
    "routine": true,
    "fresh_knowledge": false,
    "reasoning_heavy": false,
    "critical": false,
    "sensitive_data": false
  },
  "explanation": "The loaded policy prohibits model use for this input.",
  "suggested_models": {"local": [], "cloud": []}
}
```

## Focused validation

```powershell
python -m unittest tests.test_placement_v2 -v
python -m unittest tests.test_api tests.test_placement tests.test_policy tests.test_privacy -v
python -m compileall -q app tests
```

The v2 tests identify mocked classifier results as classifier fixtures. They
separately assert that the endpoint does not call local/cloud answer helpers,
that the standalone import does not import the scheduler, and that routine
logs omit synthetic prompt, failure, and credential canaries.
