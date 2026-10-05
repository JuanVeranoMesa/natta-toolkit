# Optional local-model research

This isolated component returns bounded choice data and measures selection quality.
It dispatches no capability and is not the production semantic provider. Prior
Qwen/OpenJEV experiments were rejected for production routing; this extraction
ships reproducible definitions/code, not installed runtimes or benchmark results.
Candidate scores/concentration are not calibrated correctness probabilities.

Requirements: existing uv-managed Python 3.12, uv, Apple Silicon MPS, torch,
transformers with Qwen3 support, huggingface-hub and pinned OpenJEV. The checked-in
`pyproject.toml`, `runtime.toml` and `uv.lock` define the isolated stack. Core uses
no ML imports or packages. No model weights or OpenJEV source are vendored.

Explicit optional setup, from this directory:

```sh
python3 -B bootstrap.py
.venv/bin/python -I -B decision.py --smoke
.venv/bin/python -I -B download.py --verify
```

Bootstrap uses `uv sync --locked --python 3.12 --managed-python --no-python-downloads`;
it fails if prerequisites are absent rather than repairing them. It installs
upstream code (including its build hooks), then explicitly downloads the pinned
Qwen3-0.6B snapshot from the official endpoint with credentials disabled. Files
are checked against upstream identities and a local receipt. Review dependencies
and licenses before setup. Pinned OpenJEV and Qwen identities are in
`../natta/local_model.py`; 1.7B is a separately explicit comparison profile.

Models/receipts use `~/Library/Application Support/NattaToolkit/models/`.
Inference requires MPS/FP32 and local files, uses offline environment controls,
and has no CPU/cloud fallback. Offline environment flags are not an OS network
sandbox. Doctor only probes metadata/presence without loading weights or repairing
anything; optional omissions do not fail core health.

A JSON request to `decision.py --request PATH` has exactly `state`, `instructions`
and `choices` (2–26 IDs/descriptions). Unknown/nonmaximal selections and malformed
probability distributions are rejected. Output is data only, never execution.

Fresh research commands, after explicit setup:

```sh
.venv/bin/python -I -B evaluate.py
.venv/bin/python -I -B evaluate_selection.py --help
python3 -B evaluate_codex.py --help
```

Real Codex evaluation requires an exact declared external-request budget and model
access; help/tests do not call a model. New outputs are explicit local artifacts.
Historical comparison runners require omitted baseline evidence and original stack
versions; they are research-only, not public installation steps. See
[evaluation provenance](../../docs/EVALUATION.md) and
[dependencies/licenses](../../docs/PROVENANCE.md). Normal core regression tests mock
all provider/model activity and require no ML installation.
