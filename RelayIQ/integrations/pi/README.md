# RelayIQ for Pi

This Pi package applies RelayIQ placement decisions to normal Pi prompts without
changing Pi core. The persistent Pi session, conversation history, tools, and
repository access stay on a configured local model.

When RelayIQ selects cloud work, the extension makes a separate, stateless model
completion. That Cloud Advisor receives exactly one message containing
`permitted_payload`. It receives no Pi history, system prompt, tools, tool
results, files, or images. The local Pi model receives the advisory and remains
responsible for verification and execution.

## Requirements

- RelayIQ running locally; see the repository README
- Pi 0.84.4 or compatible
- At least one configured local Pi model
- Optional configured cloud model and credentials

## Install

From the RelayIQ repository:

```powershell
Set-Location integrations\pi
npm install --ignore-scripts
pi install .
```

For a one-off run without installing:

```powershell
pi -e .\integrations\pi
```

## Configure

Model IDs are exact, case-sensitive `provider/model` values from
`pi --list-models`. RelayIQ and the extension do not infer whether a provider is
local or cloud.

```powershell
$env:RELAYIQ_PI_URL = "http://127.0.0.1:8000"
$env:RELAYIQ_PI_LOCAL_MODELS = "llama-cpp/phi-4-mini"
$env:RELAYIQ_PI_CLOUD_MODELS = "openai/gpt-5.2,anthropic/claude-sonnet-4-5"
$env:RELAYIQ_PI_TIMEOUT_MS = "10000"
pi
```

The same values can be passed as Pi flags:

```powershell
pi `
  --relayiq-url http://127.0.0.1:8000 `
  --relayiq-local-models llama-cpp/phi-4-mini `
  --relayiq-cloud-models openai/gpt-5.2,anthropic/claude-sonnet-4-5
```

`RELAYIQ_PI_CLOUD_BUDGET_KB` and `--relayiq-cloud-budget-kb` accept:

- `observed` or unset: use RelayIQ's service-observed remaining budget
- `unbounded` or `null`: explicitly omit a caller limit
- A nonnegative number: set a request-specific limit

## Behavior

| RelayIQ placement | Pi behavior |
|---|---|
| `local` | Select the suggested configured local model and continue normally |
| `cloud` | Run an isolated Cloud Advisor and give its output to the Local Executor |
| `cloud_with_consent` | Show the exact permitted payload; invoke the advisor only after approval |
| `deferred` | Continue locally and show the deferral reason |

Consent-gated work stays local when Pi has no dialog-capable UI. Image prompts,
mid-stream steering/follow-up messages, unavailable models, missing
authentication, service errors, timeouts, malformed responses, and aborted
requests never trigger Cloud Advisor execution.

## Commands

- `/relayiq-status` shows endpoint, lanes, and last non-sensitive decision metadata
- `/relayiq-on` enables placement for the current session
- `/relayiq-off` disables placement calls while keeping the session on the Local Executor

Enablement and decision metadata survive session restore and fork. Audit entries
do not contain the original prompt, permitted payload, advisor output, or
detected identifier values. RelayIQ enforces the configured local model at
session start and after model-selection attempts even when placement calls are
disabled.

## Security boundary

The default endpoint is loopback. Cleartext HTTP is rejected for non-loopback
hosts; use HTTPS for a remote RelayIQ service.

The Cloud Advisor is deliberately not a cloud coding agent. It cannot inspect
the repository or call tools. Adding sanitized multi-turn history or cloud tool
access requires a future structured egress contract; it must not be implemented
by forwarding the existing Pi session.

## Validate

```powershell
Set-Location integrations\pi
npm run check
npm test
```
