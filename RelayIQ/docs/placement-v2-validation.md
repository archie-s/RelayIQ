# Validate the RelayIQ placement contract from PowerShell

This guide covers Placement API v2 Draft 4 with the bundled
`relayiq-judge-v1` policy. Use it to check request validation, classification,
placement, model suggestions, constraints, consent, and errors.

Expected placements assume the unchanged bundled policy: local and cloud are
permitted, eligible local failure handling is allowed, and the exact demo deny
input is configured. A custom policy can produce different results.

All examples use synthetic data. `POST /v2/placement/decide` makes a placement
decision; it does **not** invoke an answer model, execute tools, obtain consent,
or redact a payload. No cloud-provider credentials are needed for these checks.

Reference: [contract](placement-v2-contract.html) |
[configuration and implementation notes](relayiq-v2-poc.md).

### Coverage at a glance

| Scenarios | Coverage |
|---|---|
| 01-03 | Defaults, preferences, omitted mappings, and both model lists |
| 04-08 | Classification flags, email-only detection, and independent consent |
| 09-13 | Connectivity, UTF-8 budgets, missing lanes, and no eligible model |
| 14-15 | Exact policy blocks and strict HTTP 400 request validation |
| 16-21 | Controlled consent, degradation, HTTP 500/503, and policy configuration |

Complete setup and define the helper first, then use the
[normal-server scenarios](#normal-server-scenarios) or the
[controlled scenarios](#controlled-scenarios-consent-degradation-and-server-errors).

## 1. Start the normal service

Open PowerShell in this repository's root, using the Python environment with
the repository requirements installed. Start Foundry and preload your Judge.
For the CPU configuration tested on a VM:

```powershell
foundry server start
# First use only: downloads approximately 4.9 GB if this variant is not cached.
foundry model download Phi-4-mini-instruct-generic-cpu:5
foundry model load Phi-4-mini-instruct-generic-cpu:5

$env:JUDGE_MODEL = "Phi-4-mini-instruct-generic-cpu"
$env:JUDGE_TIMEOUT_SECONDS = "30"
$env:RELAYIQ_POLICY_CONFIG = (Resolve-Path .\config\relayiq-policy.json).Path

python -m uvicorn app.api:app --host 127.0.0.1 --port 8891
```

Keep this terminal open. Use a **second PowerShell window** for requests.
The API documentation is at <http://127.0.0.1:8891/docs>; the bare `/` URL is
not the API's UI. Use `app.api:app`, not the legacy `app.main:app`.

`JUDGE_MODEL` selects the classifier. Request `model_mapping` lists candidate
**answer** models and does not change the Judge. Pin an exact API model ID when
multiple Foundry variants are loaded. Restart the API after configuration
changes. Foundry's endpoint, shown by `foundry server status`, is separate from
RelayIQ's port `8891`.

The 30-second setting is an inference inactivity timeout, not a total request
deadline; discovery and model loading happen separately. Preloading avoids
confusing model-loading time with classification latency.

## 2. Define the request helper once

Paste this into the request window. It works with Windows PowerShell 5.1 and
PowerShell 7, sends UTF-8 JSON, and displays HTTP errors as well as successes.
Connection failures, timeouts, and non-JSON responses remain visible exceptions.

The result wrapper contains `Name`, `StatusCode`, `ElapsedMs`, and `Body`.
**Only `Body` is the actual RelayIQ response**; the other fields are client-side
test information. The helper's 120-second HTTP timeout is not a service SLA.

```powershell
Add-Type -AssemblyName System.Net.Http
$script:RelayIQBaseUrl = "http://127.0.0.1:8891"
$FaultBaseUrl = "http://127.0.0.1:8892"

$handler = [System.Net.Http.HttpClientHandler]::new()
$handler.UseProxy = $false
$script:RelayIQClient = [System.Net.Http.HttpClient]::new($handler)
$script:RelayIQClient.Timeout = [TimeSpan]::FromSeconds(120)

$Models = @{
    local = @("Phi-4-mini-instruct-generic-cpu")
    cloud = @("nvidia/nemotron-3.5-lightning-30b-a3b")
}

function Invoke-RelayIQCheck {
    [CmdletBinding(DefaultParameterSetName = "Object")]
    param(
        [Parameter(Mandatory = $true, ParameterSetName = "Object")]
        [AllowEmptyCollection()]
        [object]$Request,
        [Parameter(Mandatory = $true, ParameterSetName = "Raw")]
        [AllowEmptyString()]
        [string]$RawJson,
        [string]$Name = "manual",
        [string]$BaseUrl = $script:RelayIQBaseUrl
    )

    $json = if ($PSCmdlet.ParameterSetName -eq "Raw") {
        $RawJson
    } else {
        ConvertTo-Json -InputObject $Request -Depth 12 -Compress
    }
    $content = [System.Net.Http.StringContent]::new(
        $json, [System.Text.Encoding]::UTF8, "application/json"
    )
    $response = $null
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $url = $BaseUrl.TrimEnd("/") + "/v2/placement/decide"
        $response = $script:RelayIQClient.PostAsync($url, $content).GetAwaiter().GetResult()
        $text = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        $watch.Stop()
        [pscustomobject]@{
            Name = $Name
            StatusCode = [int]$response.StatusCode
            ElapsedMs = [math]::Round($watch.Elapsed.TotalMilliseconds)
            Body = ($text | ConvertFrom-Json -ErrorAction Stop)
        }
    } finally {
        $content.Dispose()
        if ($null -ne $response) { $response.Dispose() }
    }
}
```

Each scenario below supplies its own request. Run them individually; do not
send a burst of concurrent LLM requests.

## 3. How to judge the results

**HTTP 200 alone is not a passing classification test.** Some checks depend on
the LLM's assessment. The expected model flags below are test expectations,
not guaranteed outputs. A wrong healthy flag is a model-quality failure; an
invalid response shape is a contract failure.

If `CLASSIFIER_DEGRADED` appears, the Judge did not produce a usable assessment.
Do not count a model-assessment scenario as passed. The response must omit the
entire `classification`, `sensitive_data_types`, and `SENSITIVE_DATA_DETECTED`.
This is **not** a finding that the input has no email. Request/policy-driven
placement checks can still be inspected, but record the degradation separately.

### Check these invariants on every response

| Check | Expected |
|---|---|
| Every HTTP 200 | UUID `decision_id`, nonblank `policy_revision`, nonempty unique `reason_codes`, `target_class`, `fallback_action`, and `requirements`. Normal-server revision is `relayiq-judge-v1`. |
| Consent | `requirements` contains only boolean `consent_required`; it is true **if and only if** `target_class` is `cloud_with_consent`. |
| Healthy classification | Exactly five booleans: `routine`, `fresh_knowledge`, `reasoning_heavy`, `critical`, `sensitive_data`. They need not be mutually exclusive. |
| Healthy email finding | `sensitive_data=true`, `SENSITIVE_DATA_DETECTED`, and exactly `sensitive_data_types=["email_address"]` for this Judge version. |
| Healthy negative finding | `sensitive_data=false`; omit the sensitivity reason and category field, not `null` or an empty array. |
| No request `model_mapping` | Omit `suggested_models` completely, including cloud, consent, and fallback decisions. |
| Mapping supplied, normal target | Return both `local` and `cloud` arrays in their submitted order. A missing request lane becomes `[]`. The selected lane must be nonempty. |
| Mapping supplied, `target_class=fallback` | `suggested_models` is `{"local":[],"cloud":[]}`. |
| Inventory versus permission | The unselected list is inventory, not permission to run that lane. `cloud_with_consent` selects from the cloud list. Respect `fallback_action`; `stop` means no answer-model execution. |
| Policy block | `POLICY_BLOCKED` requires `target_class=fallback` and `fallback_action=stop`. |
| Error response | HTTP 400/500/503 body contains exactly `error`, `message`, and `decision_id:null`, not a usable decision or model suggestions. |
| Retired fields/codes | Never return `DECISION_UNAVAILABLE`, `REDACTION_UNSUPPORTED`, or `requirements.redaction_profile`. Internal `email_detected` and raw `requires_consent` are not public response fields. |

Compare required reason codes as a set, not by order. Do not match a literal
UUID or exact `explanation` wording; explanations are optional and not machine
decision signals. The API category vocabulary remains extensible, although
the current Judge emits only `email_address`.

## Normal-server scenarios

### 01. Minimal request: default local routing and no suggestions

**Purpose:** exercise omitted options and a straightforward task.

```powershell
Invoke-RelayIQCheck -Name "01-minimal" -Request @{
    input = "What is 12 + 15? Reply with only the number."
} | ConvertTo-Json -Depth 12
```

**Expected:** HTTP 200. A healthy Judge should identify routine work, resulting
in `target_class=local` and `LOCAL_CAPABILITY_SUFFICIENT`. There must be no
`suggested_models`, because no mapping was supplied. The API does not return
the arithmetic answer.

Example **Body** for a healthy result; the UUID and model-assessed flags can vary:

```json
{
  "decision_id": "00000000-0000-4000-8000-000000000001",
  "target_class": "local",
  "policy_revision": "relayiq-judge-v1",
  "requirements": {"consent_required": false},
  "fallback_action": "local",
  "reason_codes": ["LOCAL_CAPABILITY_SUFFICIENT"],
  "classification": {
    "routine": true,
    "fresh_knowledge": false,
    "reasoning_heavy": false,
    "critical": false,
    "sensitive_data": false
  }
}
```

### 02. Explicit local preference: preserve both ordered model lists

The backup names are synthetic inventory labels, not installed-model claims.

```powershell
Invoke-RelayIQCheck -Name "02-local-inventory" -Request @{
    input = "What is 12 + 15?"
    preferred_target_class = "local"
    model_mapping = @{
        local = @("Phi-4-mini-instruct-generic-cpu", "example-local-backup")
        cloud = @("nvidia/nemotron-3.5-lightning-30b-a3b", "example-cloud-backup")
    }
} | ConvertTo-Json -Depth 12
```

**Expected:** HTTP 200, `local`, `PREFERENCE_HONORED`, consent false, and
`fallback_action=local`. Both suggestion arrays must equal the submitted
arrays, including order. Cloud inventory must remain present even though the
recommendation is local.

### 03. Explicit cloud preference, with and without mapping

```powershell
Invoke-RelayIQCheck -Name "03-cloud-mapped" -Request @{
    input = "What is 12 + 15?"
    preferred_target_class = "cloud"
    model_mapping = $Models
} | ConvertTo-Json -Depth 12

Invoke-RelayIQCheck -Name "03-cloud-unmapped" -Request @{
    input = "What is 12 + 15?"
    preferred_target_class = "cloud"
} | ConvertTo-Json -Depth 12
```

**Expected:** HTTP 200, `PREFERENCE_HONORED`, and a cloud-family target.
For this benign input, the expected assessment is `cloud` with consent false.
If the Judge requires approval, `cloud_with_consent` with consent true is
structurally valid; record that assessment rather than assuming cloud always
means approval-free execution.

The first response preserves both model lists. The second must omit
`suggested_models` entirely.

### 04. Fresh knowledge, heavy reasoning, and critical classification

These are **model-assessment tests**. No current data is fetched and no advice
or answer is generated. The emergency sentence is a synthetic test input.

```powershell
Invoke-RelayIQCheck -Name "04-fresh" -Request @{
    input = "What is the current weather in Seattle right now?"
    model_mapping = $Models
} | ConvertTo-Json -Depth 12

Invoke-RelayIQCheck -Name "04-reasoning" -Request @{
    input = "Compare serializable isolation and optimistic concurrency for a transfer workflow. Analyze write skew, failure modes, and correctness step by step."
    model_mapping = $Models
    context = @{ required_capabilities = @("extended_reasoning") }
} | ConvertTo-Json -Depth 12

Invoke-RelayIQCheck -Name "04-critical-local" -Request @{
    input = "My coworker has crushing chest pain and cannot breathe. What should I do?"
    preferred_target_class = "local"
    model_mapping = $Models
} | ConvertTo-Json -Depth 12
```

| Request | Expected healthy assessment and placement |
|---|---|
| Fresh | `fresh_knowledge=true`; default routing selects the cloud family and includes `CLOUD_CAPABILITY_REQUIRED`. |
| Reasoning | `reasoning_heavy=true`; default routing selects the cloud family. `required_capabilities` is a hint, not a tool invocation or a hard model-catalog filter. |
| Critical/local | `critical=true`, but the eligible local preference is honored. Target is local, consent false, and `PREFERENCE_HONORED` is present. Criticality does not itself authorize cloud disclosure. |

### 05. Email detection, with local placement

**Purpose:** the original HR/email case. This is an LLM detection check, not a
rule that every type of personal information sets `sensitive_data`.

```powershell
Invoke-RelayIQCheck -Name "05-email" -Request @{
    input = @'
Draft an internal HR summary of this employee record, retaining the identifying fields:
Name: Alex Example
Email address: alex.example@example.com
Employee ID: EMP-1042
Department: Finance
'@
    preferred_target_class = "local"
} | ConvertTo-Json -Depth 12
```

**Expected:** HTTP 200, local, consent false, `PREFERENCE_HONORED`, and:

```json
{
  "classification": {
    "routine": true,
    "fresh_knowledge": false,
    "reasoning_heavy": false,
    "critical": false,
    "sensitive_data": true
  },
  "sensitive_data_types": ["email_address"],
  "reason_codes": ["PREFERENCE_HONORED", "SENSITIVE_DATA_DETECTED"]
}
```

This is a **response excerpt**, not a complete response. The four other
classification flags remain model assessments; the email flag and category are
the focus. No suggestions are returned because mapping was omitted. Detecting
an email does not force cloud placement, and local placement requires no cloud
consent.

### 06. Non-email identifiers and an email-topic-only prompt

The SSN-shaped number below is an intentionally invalid synthetic example.

```powershell
Invoke-RelayIQCheck -Name "06-non-email-identifiers" -Request @{
    input = "Review confidential employee ID EMP-1042 and social security number 000-12-3456."
    preferred_target_class = "local"
    model_mapping = $Models
} | ConvertTo-Json -Depth 12

Invoke-RelayIQCheck -Name "06-email-topic" -Request @{
    input = "Explain how email addresses work without giving any example addresses."
    preferred_target_class = "local"
} | ConvertTo-Json -Depth 12
```

**Expected:** healthy `sensitive_data=false` and neither sensitivity reason nor
`sensitive_data_types`. Both requests select local. The first retains both
model lists; the second omits suggestions. A mention of the word "email" is
not an email address.

This narrow finding does not make the employee information safe to disclose.
Caller redaction and consent rules remain broader than email detection.

### 07. Cloud consent is independent of email detection

```powershell
Invoke-RelayIQCheck -Name "07-email-cloud-consent" -Request @{
    input = "Prepare a confidential HR review for Alex Example, email alex.example@example.com, employee ID EMP-1042, including private salary information."
    preferred_target_class = "cloud"
    model_mapping = $Models
} | ConvertTo-Json -Depth 12

Invoke-RelayIQCheck -Name "07-non-email-cloud-consent" -Request @{
    input = "Prepare a confidential HR review of employee ID EMP-1042 and private salary information."
    preferred_target_class = "cloud"
    model_mapping = $Models
} | ConvertTo-Json -Depth 12
```

**Expected model assessment:** these confidential records require approval,
so `cloud_with_consent`, consent true, and `PREFERENCE_HONORED` are expected.
The email request should have a positive email finding; the non-email request
should have a healthy negative finding. Both retain both model lists.

Consent is an independent LLM assessment, not a consequence of the sensitivity
reason code. If the Judge returns `cloud` with consent false, the consent branch
was not exercised; record a model-assessment issue rather than pretending this
test passed. Scenario 16 checks both consent branches with controlled model
fixtures when deterministic contract validation is needed.

### 08. Local preference can override a cloud-oriented assessment

```powershell
Invoke-RelayIQCheck -Name "08-local-over-fresh" -Request @{
    input = "What is the current weather in Seattle right now?"
    preferred_target_class = "local"
    model_mapping = $Models
} | ConvertTo-Json -Depth 12
```

**Expected:** local and `PREFERENCE_HONORED`, even if the healthy classification
says `fresh_knowledge=true`. Classification flags must not be rewritten to
match the selected target. This does not promise the local answer model can
actually obtain current weather.

### 09. Offline: override cloud preference but retain cloud inventory

```powershell
Invoke-RelayIQCheck -Name "09-offline" -Request @{
    input = "What is 12 + 15?"
    preferred_target_class = "cloud"
    model_mapping = $Models
    context = @{ connectivity = $false }
} | ConvertTo-Json -Depth 12
```

**Expected:** local, consent false, `CONNECTIVITY_UNAVAILABLE`,
`PREFERENCE_OVERRIDDEN`, and local fallback permission. Both model lists remain
present. The returned cloud list does not authorize an offline cloud call.

### 10. Budget: exact UTF-8 boundary, one byte below it, and zero

Use a non-ASCII character to catch implementations that count characters
instead of the original input's UTF-8 bytes. Budgets are bytes divided by 1024,
not JSON-envelope size or redacted-payload size.

```powershell
$text = "Summarize caf" + [char]0x00E9 + "."
$bytes = [System.Text.Encoding]::UTF8.GetByteCount($text)

Invoke-RelayIQCheck -Name "10-budget-exact" -Request @{
    input = $text
    preferred_target_class = "cloud"
    model_mapping = $Models
    context = @{ cloud_budget_kb = ([double]$bytes / 1024) }
} | ConvertTo-Json -Depth 12

Invoke-RelayIQCheck -Name "10-budget-one-byte-short" -Request @{
    input = $text
    preferred_target_class = "cloud"
    model_mapping = $Models
    context = @{ cloud_budget_kb = ([double]($bytes - 1) / 1024) }
} | ConvertTo-Json -Depth 12

Invoke-RelayIQCheck -Name "10-budget-zero" -Request @{
    input = $text
    preferred_target_class = "cloud"
    model_mapping = $Models
    context = @{ cloud_budget_kb = 0 }
} | ConvertTo-Json -Depth 12
```

**Expected:** equality permits the cloud family with `PREFERENCE_HONORED` and
no `CLOUD_BUDGET_EXCEEDED`. One byte below, or zero, selects local with
`CLOUD_BUDGET_EXCEEDED` and `PREFERENCE_OVERRIDDEN`. All three retain both model
lists. Omitted or `null` budget is unbounded for this request.

### 11. Missing mapping lanes become empty arrays

```powershell
Invoke-RelayIQCheck -Name "11-local-only" -Request @{
    input = "What is 12 + 15?"
    preferred_target_class = "cloud"
    model_mapping = @{ local = $Models.local }
} | ConvertTo-Json -Depth 12

Invoke-RelayIQCheck -Name "11-cloud-only" -Request @{
    input = "What is 12 + 15?"
    preferred_target_class = "local"
    model_mapping = @{ cloud = $Models.cloud }
} | ConvertTo-Json -Depth 12
```

**Expected:** both include `NO_ELIGIBLE_MODEL` and `PREFERENCE_OVERRIDDEN`.
Local-only selects local and returns `cloud: []`. Cloud-only selects the
cloud family and returns `local: []`; its `fallback_action` is `stop` because
there is no local candidate.

### 12. An empty mapping is not an omitted mapping

```powershell
Invoke-RelayIQCheck -Name "12-empty-mapping" -Request @{
    input = "What is 12 + 15?"
    model_mapping = @{}
} | ConvertTo-Json -Depth 12
```

**Expected:** HTTP 200, `target_class=fallback`, `fallback_action=stop`,
consent false, `NO_ELIGIBLE_MODEL`, and:

```json
{"suggested_models": {"local": [], "cloud": []}}
```

The absence of candidates is a completed decision, not a service error.

### 13. No eligible lane after connectivity constraints

```powershell
Invoke-RelayIQCheck -Name "13-cloud-only-offline" -Request @{
    input = "What is 12 + 15?"
    preferred_target_class = "cloud"
    model_mapping = @{ cloud = $Models.cloud }
    context = @{ connectivity = $false }
} | ConvertTo-Json -Depth 12
```

**Expected:** fallback/stop, consent false, `CONNECTIVITY_UNAVAILABLE`,
`NO_ELIGIBLE_MODEL`, and `PREFERENCE_OVERRIDDEN`. Because this is a fallback
response with a supplied mapping, both suggestion arrays are empty, even
though a cloud candidate was submitted.

### 14. Explicit policy block, including omission on fallback

```powershell
Invoke-RelayIQCheck -Name "14-block-mapped" -Request @{
    input = "Demonstrate the configured demo block."
    model_mapping = $Models
} | ConvertTo-Json -Depth 12

Invoke-RelayIQCheck -Name "14-block-unmapped" -Request @{
    input = "Demonstrate the configured demo block."
} | ConvertTo-Json -Depth 12

Invoke-RelayIQCheck -Name "14-different-case" -Request @{
    input = "demonstrate the configured demo block."
    preferred_target_class = "local"
    model_mapping = $Models
} | ConvertTo-Json -Depth 12
```

**Expected:** the first two return HTTP 200, `POLICY_BLOCKED`, fallback/stop,
and consent false. The mapped response contains two empty suggestion arrays;
the unmapped response has no suggestion field. Classification is still
included if healthy, or omitted with `CLASSIFIER_DEGRADED`.

The third request selects local and does not have `POLICY_BLOCKED`. The demo
deny list uses exact, case-sensitive matching; it is not general content
moderation. A valid policy denial is not the missing-policy HTTP 500 case.

### 15. Invalid requests: strict schema and retired fields

These should be rejected before the Judge is called. Each response must be
HTTP 400 with only `error=INVALID_REQUEST`, a diagnostic `message`, and
`decision_id=null`. Check the error code and shape, not exact message wording.

```powershell
$invalidCases = @(
    @{ Name = "15-missing-input"; Request = @{} }
    @{ Name = "15-blank-input"; Request = @{ input = " " } }
    @{ Name = "15-unknown-field"; Request = @{ input = "Hello"; debug = $true } }
    @{ Name = "15-retired-profile"; Request = @{
        input = "Hello"; context = @{ redaction_profiles = @("demo-redaction-v1") }
    } }
    @{ Name = "15-null-mapping"; Request = @{ input = "Hello"; model_mapping = $null } }
    @{ Name = "15-null-preference"; Request = @{ input = "Hello"; preferred_target_class = $null } }
    @{ Name = "15-response-target-as-preference"; Request = @{
        input = "Hello"; preferred_target_class = "cloud_with_consent"
    } }
    @{ Name = "15-duplicate-model"; Request = @{
        input = "Hello"; model_mapping = @{ local = @("same-model", "same-model") }
    } }
    @{ Name = "15-blank-model"; Request = @{
        input = "Hello"; model_mapping = @{ local = @(" ") }
    } }
    @{ Name = "15-string-boolean"; Request = @{
        input = "Hello"; context = @{ connectivity = "false" }
    } }
    @{ Name = "15-negative-budget"; Request = @{
        input = "Hello"; context = @{ cloud_budget_kb = -1 }
    } }
    @{ Name = "15-string-budget"; Request = @{
        input = "Hello"; context = @{ cloud_budget_kb = "1" }
    } }
    @{ Name = "15-scalar-capabilities"; Request = @{
        input = "Hello"; context = @{ required_capabilities = "extended_reasoning" }
    } }
    @{ Name = "15-null-context"; Request = @{ input = "Hello"; context = $null } }
    @{ Name = "15-input-too-long"; Request = @{ input = ("x" * 100001) } }
)

foreach ($case in $invalidCases) {
    Invoke-RelayIQCheck -Name $case.Name -Request $case.Request |
        ConvertTo-Json -Depth 12
}

Invoke-RelayIQCheck -Name "15-malformed-json" -RawJson '{"input":' |
    ConvertTo-Json -Depth 12

Invoke-RelayIQCheck -Name "15-empty-body" -RawJson "" |
    ConvertTo-Json -Depth 12

Invoke-RelayIQCheck -Name "15-non-object-json" -RawJson '[]' |
    ConvertTo-Json -Depth 12
```

## Controlled scenarios: consent, degradation, and server errors

You cannot request a policy failure or service outage through a valid request
field. Do not try to cause one by stopping a shared Foundry service or editing
the normal policy.

Instead, use the following **test-only server on port 8892**. It runs the real
API, Judge adapter, and policy code with explicit injected model/failure
fixtures. It makes **no real LLM calls** and does not alter the normal service.
These checks validate contract branches, not model quality.

Open a **third PowerShell terminal in the repository root** and define:

```powershell
$ControlledServer = @'
import json
import sys
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import uvicorn
from app.api import create_app
from app.placement import PlacementServiceUnavailableError

mode = sys.argv[1] if len(sys.argv) > 1 else ""
allowed = {
    "consent", "consent-no-email", "degraded", "missing-policy",
    "policy-error", "unavailable", "deny-all", "invalid-policy",
}
if mode not in allowed:
    raise SystemExit("Choose one of: " + ", ".join(sorted(allowed)))
port = int(sys.argv[2]) if len(sys.argv) > 2 else 8892
policy_path = Path(r"config\relayiq-policy.json")
signals = {
    "routine": True, "fresh_knowledge": False, "reasoning_heavy": False,
    "critical": False, "email_detected": mode == "consent",
    "requires_consent": mode in {"consent", "consent-no-email"},
}

with ExitStack() as stack:
    if mode in {"deny-all", "invalid-policy"}:
        temp = Path(stack.enter_context(TemporaryDirectory(prefix="relayiq-validation-")))
        document = json.loads(policy_path.read_text(encoding="utf-8"))
        document["policy_revision"] = "validation-" + mode
        if mode == "deny-all":
            document["permitted_targets"] = []
        else:
            document["supported_redaction_profiles"] = []
        policy_path = temp / "policy.json"
        policy_path.write_text(json.dumps(document), encoding="utf-8")
    if mode == "missing-policy":
        stack.enter_context(patch("app.api._policy_from_setting", return_value=None))

    api = create_app(policy_path)
    if mode == "degraded":
        model = patch("app.foundry.chat_local", side_effect=RuntimeError("Injected Judge failure"))
    else:
        model = patch("app.foundry.chat_local", return_value=(json.dumps(signals), 0))
    stack.enter_context(model)
    if mode == "policy-error":
        stack.enter_context(patch("app.policy.evaluate_v2", side_effect=RuntimeError("Injected policy failure")))
    if mode == "unavailable":
        stack.enter_context(patch.object(
            api.state.placement_engine, "decide_v2",
            side_effect=PlacementServiceUnavailableError("Injected service outage"),
        ))
    print(f"TEST-ONLY mode={mode} at http://127.0.0.1:{port}", flush=True)
    uvicorn.run(api, host="127.0.0.1", port=port)
'@
```

Run **one mode at a time** using the startup command in each scenario.
Before changing modes, press **Ctrl+C in this third terminal**, then run the
next command. The request commands belong in the second terminal, where the
helper and `$FaultBaseUrl` are defined.

If port 8892 is already in use, choose an unused port in both the startup
command and `$FaultBaseUrl`. Do not replace the normal API with this fixture.

### 16. Guaranteed consent branches, with and without email

Third terminal:

```powershell
$ControlledServer | python - consent 8892
```

Request terminal:

```powershell
Invoke-RelayIQCheck -Name "16-controlled-email-consent" -BaseUrl $FaultBaseUrl -Request @{
    input = "Review confidential email alex.example@example.com."
    preferred_target_class = "cloud"
    model_mapping = $Models
} | ConvertTo-Json -Depth 12

Invoke-RelayIQCheck -Name "16-controlled-consent-unmapped" -BaseUrl $FaultBaseUrl -Request @{
    input = "Review confidential email alex.example@example.com."
    preferred_target_class = "cloud"
} | ConvertTo-Json -Depth 12
```

**Expected:** HTTP 200, `cloud_with_consent`, consent true,
`PREFERENCE_HONORED`, `SENSITIVE_DATA_DETECTED`, a healthy positive
classification, and `["email_address"]`. The first response preserves both model
lists; the unmapped request must omit `suggested_models`, even on this consent
target.

Stop the controlled server, then start the second consent fixture:

```powershell
$ControlledServer | python - consent-no-email 8892
```

```powershell
Invoke-RelayIQCheck -Name "16-controlled-non-email-consent" -BaseUrl $FaultBaseUrl -Request @{
    input = "Review confidential employee ID EMP-1042."
    preferred_target_class = "cloud"
    model_mapping = $Models
} | ConvertTo-Json -Depth 12
```

**Expected:** `cloud_with_consent` and consent true, but healthy
`sensitive_data=false` with neither sensitivity reason nor category field.
This proves that the consent requirement is independent of email detection.

### 17. Degraded classifier, including consent without a sensitivity finding

Third terminal:

```powershell
$ControlledServer | python - degraded 8892
```

Request terminal:

```powershell
foreach ($target in @("local", "cloud")) {
    Invoke-RelayIQCheck -Name "17-degraded-$target" -BaseUrl $FaultBaseUrl -Request @{
        input = "Review employee ID EMP-1042 and email alex.example@example.com."
        preferred_target_class = $target
        model_mapping = $Models
    } | ConvertTo-Json -Depth 12
}
```

**Expected:** HTTP 200 and `CLASSIFIER_DEGRADED` in both cases. Omit
`classification`, `sensitive_data_types`, and `SENSITIVE_DATA_DETECTED`,
even though the input contains an email.

The local request selects local with consent false. The cloud request selects
`cloud_with_consent` with consent true because the fallback's broader privacy
checks still apply. Both preserve the two model lists. Degraded classification
is not the same as `target_class=fallback`.

### 18. Missing policy is HTTP 500, not a fallback decision

Third terminal:

```powershell
$ControlledServer | python - missing-policy 8892
```

Request terminal:

```powershell
Invoke-RelayIQCheck -Name "18-missing-policy" -BaseUrl $FaultBaseUrl -Request @{
    input = "What is 12 + 15?"
    model_mapping = $Models
} | ConvertTo-Json -Depth 12
```

**Expected:** HTTP 500 with exactly this **Body**:

```json
{
  "error": "INTERNAL_ERROR",
  "message": "The placement service could not complete the request.",
  "decision_id": null
}
```

There is no `target_class`, fallback instruction, classification, policy
revision, suggestions, or `DECISION_UNAVAILABLE`. Missing policy fails before
the Judge is invoked.

### 19. Policy evaluation failure is also HTTP 500

Third terminal:

```powershell
$ControlledServer | python - policy-error 8892
```

Request terminal:

```powershell
Invoke-RelayIQCheck -Name "19-policy-error" -BaseUrl $FaultBaseUrl -Request @{
    input = "What is 12 + 15?"
    model_mapping = $Models
} | ConvertTo-Json -Depth 12
```

**Expected:** the same HTTP 500 error envelope as scenario 18, even though a
valid policy exists and permits local failure handling. A policy failure must
not authorize local fallback or publish a partial decision.

### 20. Service unavailable is HTTP 503

Third terminal:

```powershell
$ControlledServer | python - unavailable 8892
```

Request terminal:

```powershell
Invoke-RelayIQCheck -Name "20-unavailable" -BaseUrl $FaultBaseUrl -Request @{
    input = "What is 12 + 15?"
} | ConvertTo-Json -Depth 12
```

**Expected:** HTTP 503, exactly `error=SERVICE_UNAVAILABLE`, a string
`message`, and `decision_id=null`. No decision or suggestions. This is an
explicit service-unavailability fixture; an ordinary Judge timeout instead
uses the degraded classification behavior in scenario 17.

### 21. Valid deny-all policy versus invalid policy configuration

A policy permitting no targets is valid configuration, not a missing policy.
The fixture uses a temporary policy file and leaves the real file unchanged.

Third terminal:

```powershell
$ControlledServer | python - deny-all 8892
```

Request terminal:

```powershell
Invoke-RelayIQCheck -Name "21-deny-all" -BaseUrl $FaultBaseUrl -Request @{
    input = "What is 12 + 15?"
    model_mapping = $Models
} | ConvertTo-Json -Depth 12
```

**Expected:** HTTP 200, `policy_revision=validation-deny-all`,
`POLICY_BLOCKED`, fallback/stop, consent false, and two empty suggestion arrays.
Healthy classification is still present.

Stop that controlled server, then run:

```powershell
$ControlledServer | python - invalid-policy 8892
```

**Expected:** startup fails visibly with `PolicyConfigurationError` because the
temporary file includes the retired `supported_redaction_profiles` setting.
No new server starts on port 8892, so there is **no HTTP response to validate**.
A configured missing/unreadable file likewise fails startup; that differs from
an absent policy setting yielding the per-request HTTP 500 in scenario 18.

## Finish and record results

For each scenario, record HTTP status, policy revision, target, consent flag,
reason codes, classification, model suggestions, and elapsed time. Note whether
the result came from the real model or a controlled fixture. Do not mark
model-quality checks as passed based only on fixture results.

Stop only the controlled server with Ctrl+C; leave the normal service running
if you are continuing testing. Dispose the request helper when finished:

```powershell
$script:RelayIQClient.Dispose()
```

These checks do not certify the caller's redaction, consent UI, provider
credentials, or answer execution. Those require separate agent integration
validation.
