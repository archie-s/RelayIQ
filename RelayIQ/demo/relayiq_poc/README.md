# RelayIQ + 007 bounded POC packaging

This folder owns only the RelayIQ-side integration packaging for D01-D03. It
creates a disposable, secret-free synthetic workspace and evidence scaffolding.
It does not implement the 007 runner, invoke a model, request credentials, or
claim that any scripted/live scenario passed.

The profile is **`demo-redaction-v1` for exact registered synthetic sources
only**. It is not `pii-v1`, general PII protection, or production readiness.

RelayIQ now implements Placement API v2 Draft 4 with policy `relayiq-judge-v1`.
The synthetic profile remains a caller-side fixture/redaction mechanism, not
an API request, response, or policy field. The 007 owner must update its client
to stop sending `context.redaction_profiles` and consuming
`requirements.redaction_profile`, and to handle the new `classification` and
conditional `sensitive_data_types` fields. A Draft 3 client is not compatible.

The current Judge detects email addresses only for sensitivity findings and
returns `["email_address"]` on a positive assessment. Non-email identifiers alone
do not trigger `SENSITIVE_DATA_DETECTED`; malformed or out-of-scope model output
degrades instead. Consent remains independent. The caller's demo redactor still
protects all of its original fixture types, including names and employee IDs.
`suggested_models` is a two-lane object, not a flat array: select from the lane
matching the recommendation, and do not treat the other list as authorization.
POC-15 now checks rejection of a retired response profile field.

The revision inventory retains original handoff hashes as historical evidence;
`relayiq_source_sha256` records the current Draft 4 policy hash separately.
Package verification does not assert that the external 007 client is compatible.

## Materialize a fresh package

Use a new absolute directory outside every Git repository. The command refuses
an existing destination, any destination beneath a `.git` file/directory, or a
dirty RelayIQ worktree so the recorded commit fully identifies the producer.

```powershell
$DemoBundle = Join-Path $env:TEMP ("relayiq-007-poc-" + [guid]::NewGuid())

python .\demo\relayiq_poc\materialize.py `
  --output-root $DemoBundle `
  --local-model-id "phi-4-mini" `
  --cloud-model-id "nvidia/nemotron-3.5-lightning-30b-a3b" `
  --relayiq-port 8891 `
  --foundry-port 5272 `
  --agent-revision "<committed-007-revision>"
```

The model names are configuration labels, not readiness evidence. Resolve the
actual loaded Foundry model before any live run. The materializer stores only
the fixed key environment-variable name `NVIDIA_API_KEY`; it never reads or
writes the key value.

Output:

```text
<bundle>\
  control\
    007-routing.json
    demo-profile.json
    relayiq-policy.json
    revision-inventory.json
  workspace\
    demo-manifest.json
    demo_employee_record.txt
  evidence\
    scenario-results.json
    human-validation-record.json
    evidence-index.json
```

`workspace` is the exact fresh 007 working directory and `${DEMO_ROOT}` value.
Do not add project documents, arbitrary files, or real user data. Start a new
007 session there with `--no-web`, no resume, and only `read_file` for
`demo_employee_record.txt`. The 007 implementation owner supplies and validates
the final runner/launch selector; this RelayIQ package does not replace it.

## Verify without models

```powershell
python .\demo\relayiq_poc\verify_materialization.py $DemoBundle
python -m unittest discover -s .\demo\relayiq_poc -p "test_*.py" -v
```

Verification checks the exact 81-byte LF fixture and frozen SHA-256, concrete
loopback configuration, bounded profile/policy identity, materialized hashes,
all 17 scripted result rows, six separate live rows, empty evidence index, and
`not_run` human-validation state.

The repository stores the frozen fixture as base64 so Windows Git checkout
conversion cannot change its LF bytes. Only the materializer writes the decoded
`demo_employee_record.txt`.

## Start only the RelayIQ service

From this repository, using the materialized policy:

```powershell
$env:RELAYIQ_POLICY_CONFIG = Join-Path $DemoBundle "control\relayiq-policy.json"
python -m uvicorn app.api:app --host 127.0.0.1 --port 8891
```

Record the PID created by the verifier and stop only that process with
`Ctrl+C`. Never stop an existing service to simulate unavailability; configure
the 007 client with a verified unused loopback port for POC-07.

The 007 owner consumes:

```text
<bundle>\control\007-routing.json
<bundle>\workspace
<bundle>\evidence\scenario-results.json
```

Before live execution, the 007 owner must confirm the configuration's
`agent_revision`, actual local model readiness, NVIDIA access through the
existing environment/UI mechanism, and a separately launched verifier-owned
UI. Missing access is `blocked`, never a scripted or live pass.

## Evidence rules

- Scripted runs update only the 17 `scripted` rows.
- Live POC-01 through POC-06 update the separate six `live` rows.
- Keep `not_run` until a run actually occurs. Use only `pass`, `fail`, or
  `blocked` afterward.
- Capture actual decisions, counters, predicate results, and sanitized
  provider-bound message bodies—not copied expected templates.
- Never retain authorization headers, key values, general environment dumps,
  arbitrary user content, or unsanitized provider-bound messages.
- Add safe artifact paths and hashes to `evidence-index.json`.
- Human validation remains `not_run` until a reviewer performs the listed
  D01-D03 checks.

For D03, the acceptance report must reconcile every scripted row and all six
live rows with the final RelayIQ/007 revisions. Any `fail`, `blocked`, or
`not_run` mandatory row prevents final POC acceptance.
