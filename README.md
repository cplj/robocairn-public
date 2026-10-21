> This repository is the physical module entry point for [Truquence](https://github.com/cplj/truquence-public). Install the main package from [PyPI: truquence](https://pypi.org/project/truquence/).

# RoboCairn 0.1.0

Compare two versions of recorded robot observations and see regressions and unresolved dimensions in one JSON report.

Python 3.10+ with Git and venv/pip is required. The twelve included cases are manual
fixtures; this quick start produces a software contract report.

## Offline quick start

On macOS/Linux, get the public source once (this step needs network access):

```sh
git clone https://github.com/cplj/robocairn-public.git
cd robocairn-public
```

From the repository root, copy the block below into a shell with Python 3.10+
and venv/pip available locally. The supplied universal wheel contains the CLI and
Python/HTTP APIs; installation needs no index, cache, setuptools or other external
package. No simulator is installed. Use the wheel command below, not `pip install .`.

```sh
# Run from the root of this repository.
ROBO_V01_ENV="$(mktemp -d)/venv"
python3 -m venv "$ROBO_V01_ENV"
. "$ROBO_V01_ENV/bin/activate"
python -m pip --isolated install --no-index --no-cache-dir --no-deps wheels/robocairn-0.1.0-py3-none-any.whl
robocairn --version
robocairn check fixtures.json > report.json || test "$?" -eq 3
python -c 'import json; r=json.load(open("report.json")); print(r["status"], r["report"]["counts"], r["report"]["rows_with_unknown"])'
```

Expected output after installation:

```text
0.1.0
UNKNOWN {'IMPROVEMENT': 2, 'REGRESSION': 3, 'UNCHANGED': 1, 'UNCHANGED_FAILURE': 1, 'UNCHANGED_UNSAFE': 1, 'UNKNOWN': 4} 5
```

The example's exit code is **3**, because five rows contain unknown dimensions.
Four are classified UNKNOWN; one has a known regression plus an unknown safety
dimension. The shell explicitly accepts exit 3 so you can inspect the report.
`report.json` is the full JSON output. The relative sample paths belong to this checkout;
after installation the CLI can run from any directory with explicit input paths.
Samples and tests stay in the source checkout, rather than the installed wheel.

Keep `wheels/robocairn-0.1.0-py3-none-any.whl` with this checkout when transferring
it to an offline machine. The wheel works with Python 3.10+ on macOS/Linux; Python
and its venv/pip support must already be installed. The flags disable package
indexes, pip cache and dependency resolution; no build environment is created.
`--isolated` ignores user pip configuration and pip environment variables. It is
not an operating-system network sandbox. Source builds via `pip install .` still
require the setuptools build tool declared in `pyproject.toml`; they are not the
offline installation path. Installing setuptools is unnecessary for this wheel.

## Common errors and missing prerequisites

| Symptom | Next step |
|---|---|
| `git` or Python missing; Python older than 3.10 | Provide Git and Python 3.10+ first; check `git --version` and `python3 --version`. |
| `venv` / `ensurepip` unavailable | Provide Python's venv/pip support before running installation; ask the machine administrator if needed. |
| Wheel or `fixtures.json` not found | Run `pwd` and `ls wheels fixtures.json` in the cloned repository root; use an absolute input path from another directory. |
| `robocairn: command not found` / import fails | Activate the same venv used for installation; `python -m robocairn_app --help` is the equivalent module entry point. |
| Missing argument or unexpected flag | Run `robocairn --help` or `robocairn check --help`; start with `robocairn check fixtures.json`. |
| `missing=[...]` / `unexpected=[...]` | Fix those fields at the input path in the message; compare with the [request schema](docs/schemas/request.schema.json). |
| `--baseline` / `--candidate` supplied alone | Supply both batch paths or omit both to replay the suite. |
| Exit 3 with `status: UNKNOWN` | Read `report.rows` and `unknown_dimensions`; this is the expected fixture result. |
| Docker unavailable or base image missing | Use the native wheel quick start; Docker needs a running engine and the pinned base image already present for offline use. |

## CLI and inputs

The main command is `robocairn check SUITE.json`. It replays the two declared
versions using the existing replay engine, then compares the records. Run
`robocairn --help` or `robocairn check --help` for usage.

To compare your own recorded batches instead of replaying the suite's observations:

```sh
robocairn check fixtures.json --baseline baseline.json --candidate candidate.json
```

Both batch options must be provided together. The suite must contain exactly
`schema_version`, `environment`, `policy`, `source`, and `fixtures`, as shown in
[`fixtures.json`](fixtures.json). Version 1 accepts 10–20 uniquely named fixtures,
uint32 seed identities (0–4294967295) and integer steps (0–10000). Observations use
`outcome` = `SUCCESS`, `FAILURE` or `UNKNOWN`, and `unsafe` = `TRUE`, `FALSE` or
`UNKNOWN` (strings, not JSON booleans). Environment/source identities and versions
must be nonempty; policy versions must be distinct. Evidence must be
`SCHEMA_REPLAY_CONTRACT_ONLY`.

Batch objects contain exactly `scope`, `policy_version` and `results`. Each result
has `id`, `seed`, `outcome`, `unsafe`, `steps`. All fixture identities and seeds must
match, with no duplicate, missing or extra results. Scope binds the suite's exact
canonical SHA256, environment/source/schema and policy identity. Generate matching
batch examples using the installed Python API:

```sh
python - <<'PY'
import robocairn as rc
from pathlib import Path
suite = rc.load('fixtures.json')
for side in ('baseline', 'candidate'):
    batch = rc.replay(suite, suite['policy'][side + '_version'])
    Path(side + '.json').write_text(rc.canonical(batch), encoding='utf-8')
PY
robocairn check fixtures.json --baseline baseline.json --candidate candidate.json > paired-report.json || test "$?" -eq 3
```

Unknown policy versions, scope drift, unexpected fields, duplicate JSON keys,
invalid JSON and nonfinite values are rejected. The original `robocairn.replay`
and `robocairn.compare` Python interfaces and source-script commands remain available.


All JSON files and HTTP bodies are limited to 1 MiB inclusive. JSON and Python
inputs allow at most 32 container levels, 4096 nodes (including object keys),
256 entries per collection and 1024 characters per string. Result batches must
have exactly as many rows as the suite (10–20). Excess input raises `ContractError`
in the Python API and returns a clear ERROR from the CLI (exit 2) or HTTP API
(400; oversized HTTP bodies return 413). Files are read with a bounded read;
excess nesting is rejected before JSON decoding. These limits bound input work;
they do not make the development server a production service.

The product has no outbound HTTP client. Its outbound host allowlist is empty:
no caller URL, redirect, private address, link-local address or metadata endpoint
is resolved or contacted. URL-looking identities remain inert observation data.
The fixed loopback HTTP clients in the tests do not follow redirects and exist
only to exercise the local server.

## Local HTTP API

In the activated environment, start the server in a terminal:

```sh
robocairn serve --port 8765
```

By default it listens only on `127.0.0.1`. Stop it with Ctrl-C. In a second terminal, from
the repository root, send the same suite as JSON:

```sh
python3 -c 'import json; json.dump({"suite": json.load(open("fixtures.json"))}, open("request.json", "w"))'
curl --silent --show-error --fail-with-body \
  -H 'Content-Type: application/json' \
  --data-binary @request.json http://127.0.0.1:8765/check
```

`POST /check` accepts `{"suite": <suite object>}`, or
`{"suite": <suite object>, "baseline": <batch object>, "candidate": <batch object>}`.
It accepts values, never file paths; it does not read files requested by clients.
The CLI and endpoint use the same `robocairn_app.check(request)` function.

Both return this envelope (the report below is abbreviated):

```json
{
  "status": "UNKNOWN",
  "error": null,
  "report": {
    "evidence": "SCHEMA_REPLAY_CONTRACT_ONLY",
    "counts": {"IMPROVEMENT": 2, "REGRESSION": 3, "UNCHANGED": 1, "UNCHANGED_FAILURE": 1, "UNCHANGED_UNSAFE": 1, "UNKNOWN": 4},
    "rows_with_unknown": 5
  }
}
```

Full reports also include scope, baseline/candidate policy versions, and every row
with its original observations, classification and `unknown_dimensions`.
An invalid input such as `{}` returns:

```json
{"status":"ERROR","report":null,"error":{"code":"INVALID_INPUT","message":"request needs suite and optionally both baseline and candidate"}}
```

| Status | Meaning | CLI exit | HTTP |
|---|---|---:|---:|
| `OK` | Complete valid comparison with no unknown dimensions; can still contain regressions | 0 | 200 |
| `UNKNOWN` with report | Valid comparison with unresolved dimensions; all known regressions remain visible | 3 | 200 |
| `ERROR` | Invalid input, file/argument error or rejected HTTP request; no report | 2 | 4xx for requests |
| `UNKNOWN` without report | Unexpected internal failure; comparison could not be completed | 3 | 500 |

Always inspect `status` and `report.counts`. HTTP 200 / CLI 0 do not mean absence
of regression, customer acceptance or safety approval. UNKNOWN must not become an
acceptance decision. Regression takes precedence over an improvement or unknown
dimension; unknown dimensions remain attached to that row. Step counts are retained
but are not used to classify regressions.

The API requires `application/json` and one Content-Length, rejects transfer
encoding, caps bodies at 1 MiB inclusive and uses a five-second socket timeout.
Oversize lengths are rejected before reading the body; clients that continue
sending after rejection may see a connection reset. Keep requests within the limit.
Malformed input/scope is HTTP 400, unknown paths 404, GET 405, wrong media type 415,
oversize input 413 and other unsupported methods 501. HTTP errors have JSON
envelopes. The local development server has no authentication or TLS and is for
trusted local callers. It is not a hosted or production network service.

## Docker: local CLI and API in one image

From the repository root, with a working local Docker engine, build and start the API:

```sh
docker build --network=none --pull=false -t robocairn:0.1.0 .
docker run -p 127.0.0.1:8765:8765 robocairn:0.1.0
```

The image starts the API by default and runs as UID/GID 65532. It copies only six
explicit product files; the default-deny `.dockerignore` excludes everything else
from the build context. There are no secrets, mounted credentials, package installs,
model downloads or application outbound network calls. The Python base image must
already be available locally for a fully offline build; Docker may fetch that base
image if it is missing. `--network=none` disables networking for build steps, not
base-image acquisition. This Dockerfile has no RUN steps.

The API binds to `0.0.0.0` **inside the container** so Docker can forward requests;
the run command publishes it only on the host's `127.0.0.1`. Native `robocairn serve`
keeps its loopback default. Docker's bridge permits network access, but the
application does not make outbound requests. For the CLI, `--network=none` also
enforces network isolation at the container level.

In a second terminal, from the repository root, call the local container API:

```sh
python3 -c 'import json; print(json.dumps({"suite": json.load(open("fixtures.json"))}))' | \
  curl --silent --show-error --fail-with-body -H 'Content-Type: application/json' \
    --data-binary @- http://127.0.0.1:8765/check
```

It returns the same UNKNOWN report shown above. Stop the foreground API with
Ctrl-C. No cloud service is involved. Docker runtime commands need no internet
access once the image exists.

For the container CLI quick start, override the image's default API arguments:

```sh
docker run --network=none robocairn:0.1.0 --version
docker run --network=none robocairn:0.1.0 check fixtures.json > docker-report.json || test "$?" -eq 3
python3 -c 'import json; r=json.load(open("docker-report.json")); print(r["status"], r["report"]["counts"], r["report"]["rows_with_unknown"])'
```

Expected output is `0.1.0` followed by the same UNKNOWN/counts/5 summary as the
native quick start. Exit 3 remains UNKNOWN, not a successful acceptance decision.
The image entry point is `python -m robocairn_app`, using the same CLI and core as
the pip installation. Fixtures are included in the image; no host mounts are needed.

## Tests

With the package installed in the active venv, run from the repository root:

```sh
python -m unittest discover -s . -p 'test_*.py' -v
```

Tests cover the predetermined oracle, scope/identity validation, UNKNOWN retention,
installed CLI from another directory, paired batches, HTTP success/error/UNKNOWN,
malformed/duplicate-key/nonfinite JSON, limits and seed/step/suite boundaries.
The stdlib property suite (`python -B test_properties.py`) uses seed 20261015,
200 generated samples per property and 2,000 checks across seven properties:
JSON roundtrip, malformed/duplicate/nonfinite rejection, schema diagnostics, input
limits, batch permutation, file/CLI errors and HTTP errors. It adds no dependency.
The internal-failure HTTP case injects an exception to verify the UNKNOWN envelope;
it is not evidence of an observed backend failure.

## What it does not do

- Execute a physics simulator, robot, policy, model, sensor or safety controller.
- Establish physics fidelity, real robot safety or real policy performance.
- Turn manually recorded `unsafe` values into measured or certified safety evidence.
- Authenticate observation provenance; digests bind records, not their origin.
- Remove uncertainty, approve a release, or prove customer value or payment.
- Download models, upload observations, provide cloud hosting or contact anyone.

The included twelve cases are manual fixtures. Their results are **schema replay
contract evidence only**, never physical simulation or robot safety evidence.

## For AI agents

Purpose: compare paired recorded observations while preserving unresolved dimensions.

Install from this checkout in an activated Python 3.10+ environment:

```sh
python -m pip --isolated install --no-index --no-cache-dir --no-deps wheels/robocairn-0.1.0-py3-none-any.whl
```

Before installing, clone the repository and create/activate the venv as shown in
[Offline quick start](#offline-quick-start). Installation uses only the supplied
wheel; no package-index release is assumed. Keep input files in the checkout or
pass absolute paths. A missing interpreter, venv/pip, wheel or input file must be
resolved before retrying.

Minimal Python call, from the repository root:

```python
import robocairn
from robocairn_app import check
result = check({"suite": robocairn.load("fixtures.json")})
print(result["status"], result["report"]["counts"])
```

Schemas: [request](docs/schemas/request.schema.json),
[response](docs/schemas/response.schema.json), and [HTTP interface](openapi.yaml).
The Python function raises `ContractError` for invalid input; the CLI and HTTP
interface wrap invalid input in an ERROR envelope. Inspect UNKNOWN and per-row
unknown dimensions before interpreting any result. The service has no access control;
keep the default loopback binding. There is no simulator, sensor acquisition or
real-world safety assessment. Recorded inputs are not authenticated.

Error contract: CLI stdout contains one JSON envelope; exit 2 means `ERROR` with
`report: null` and `error.code` / `error.message`. Argument errors use
`ARGUMENT_ERROR`, input/file errors use `INVALID_INPUT`. Fix the named cause before
retrying. Python callers should catch `ContractError` / `ValueError`, `TypeError`
or `OSError` for input/loading errors; unexpected exceptions must remain unresolved.
HTTP invalid input is 400, oversize bodies 413, wrong media type 415. An unexpected
internal failure returns `UNKNOWN` / `INTERNAL_ERROR`, null report, CLI exit 3 or
HTTP 500; it is not a completed comparison. [Full status table](#local-http-api).

Discovery: [llms.txt](llms.txt). Documentation: [readable guide](docs/README.md).
[Comparison evidence](docs/README.md#comparison-evidence): no qualified comparative result
is published in this release, and no superiority claim is made.

## Distribution and validation limits

The local wheel installation and interface examples can be exercised without a
package index. Source builds require the build tool declared in `pyproject.toml`.
Container runtime behavior has not been verified. The build and release workflows
are templates: no hosted package, registry image or online site is implied.
The publishing environment must be configured before an authorized release.

## License and security

Apache-2.0; see [LICENSE](LICENSE) and [NOTICE](NOTICE).
Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
