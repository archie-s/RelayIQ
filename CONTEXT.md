# RelayIQ Placement

RelayIQ decides where AI work may execute while keeping the caller in control of
model invocation and cloud egress.

## Language

**Placement Decision**:
A result for one submitted request. It contains one Placement Recommendation
without executing an answer model.
_Avoid_: Routing result, model response

**Placement Recommendation**:
The Judge's requested execution outcome after policy and eligibility constraints
are applied. The allowed values are `local`, `cloud`, `cloud_with_consent`, and
`fallback`.
_Avoid_: Model Lane, provider choice

**Consent Requirement**:
The Judge's conclusion that a particular cloud-bound request requires caller
approval, based on the submitted prompt and request context. It is not a
deployment-wide operator setting.
_Avoid_: Cloud consent mode, consent policy flag

**Local Executor**:
The model that retains conversation context and performs tool-backed work on the
user's device.
_Avoid_: Local Advisor, fallback model

**Cloud Advisor**:
A stateless cloud model that receives only a Permitted Payload and returns
analysis for a Local Executor to verify.
_Avoid_: Cloud Executor, remote agent

**Permitted Payload**:
The complete caller content authorized for a cloud invocation by one Placement
Decision.
_Avoid_: Prompt preview, redacted prompt

**Model Lane**:
An explicit classification of a model as local or cloud for placement purposes.
`cloud_with_consent` is a Placement Recommendation, not a third Model Lane.
_Avoid_: Provider type, model tier

**Consent Receipt**:
Non-sensitive metadata recording whether a person approved a specific
consent-gated Placement Decision.
_Avoid_: Consent token, approval payload
