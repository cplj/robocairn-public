# RoboCairn guide

Start with the [quick start](../README.md#offline-quick-start) to install the supplied
wheel and produce a JSON comparison report. Git is needed to obtain the checkout;
Python 3.10+ with venv/pip is needed to install. No simulator is required for the
manual fixture example.

## Inputs and errors

- [Request schema](schemas/request.schema.json): suite, or suite plus both batches.
- [Response schema](schemas/response.schema.json): status, report and error envelope.
- [HTTP interface](../openapi.yaml): local POST /check.
- [Agent instructions](../README.md#for-ai-agents): install, minimal call and error handling.
- [Troubleshooting](../README.md#common-errors-and-missing-prerequisites): missing prerequisites and recovery.

CLI exit 0 means a valid complete comparison, which can contain regressions.
Exit 2 means rejected input or an argument/file error; fix the named cause.
Exit 3 means UNKNOWN: inspect unresolved dimensions or the internal error before
making a decision. Python loading/validation errors raise exceptions; CLI and HTTP
return envelopes as described in the README. Never treat UNKNOWN as acceptance.

## Comparison evidence

No qualified comparative result is published in this release. The included
fixtures contain manual observations and show the software contract only.
Replay executes no simulator, policy or robot. It establishes neither physical
fidelity nor robot safety or real policy performance. Container runtime remains
unverified. Recorded input provenance is not authenticated.
