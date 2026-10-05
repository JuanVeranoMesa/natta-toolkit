# Evaluation and extraction provenance

The source implementation's user-verified regression baseline is 441 passed,
0 failed, 0 skipped. The public derivative retains all 447 test methods and all
architectural safety contracts. No source application is needed by tests. The supplied verified baseline was
441; static inventories of the current source and derivative both contain exactly
447 test methods. No test method was added or removed during extraction. The
source baseline count and current source inventory are reported separately.

Published evaluation definitions include core planner/commit/TestFlight corpora
and controls, and local-model selection corpus/descriptions/Phase 4C controls.
Fictitious project aliases/display names replace private application references.
The derivative's control hashes were recomputed for those sanitized inputs;
they are new public definitions, not byte-identical historical benchmark controls.
No production provider policy/domain was broadened by this operation.

Private host acceptance records, prompts/responses, model predictions, benchmark
results, fingerprints and machine/setup reports are excluded. No historical
benchmark accuracy is claimed for the rewritten public corpora. The original
research selected the current explicit semantic provider and rejected local
Qwen/OpenJEV routing; those decisions remain source history, not freshly measured
public-host certification.

Three tests in `test_model_profiles.py` previously loaded a historical 0.6B
benchmark. They now create complete synthetic 865-decision comparison data using
fake decisions, exercising input/model identity, summaries, comparison compatibility,
immutable-byte tampering and smoke-failure behavior. Synthetic data is generated
in disposable files and is not published as empirical model output.

`evaluate.py`, `evaluate_selection.py`, `evaluate_codex.py`, and the core
planner/commit/TestFlight evaluators retain explicit budgets and non-execution
boundaries. Real provider evaluation can cost money; no real provider call was
made during extraction validation. ML inference requires explicit setup/MPS.

Historical `compare.py`, `run_comparison.py`, `run_selection_comparison.py` and
comparison helpers remain research-only code covered by tests. Their private
baseline artifacts are intentionally absent. The baseline digest is an explicit
unconfigured marker; comparison refuses until a reviewer supplies a new baseline
and pins its digest. The historical comparison runner additionally pins its
original stack versions and is not a general setup command. Use fresh evaluation
commands for public data; do not treat historical orchestration as production.

Confinement compatibility has no inherited source fingerprints. The current
manifest pins only the derivative's own implementation; a user must perform
host-local validation. No source validation receipt or model snapshot is shipped.

Ten stability tests now create a synthetic canonical reference instead of reading
a historical provider transcript. The full-metrics comparison test creates a
synthetic baseline; the help-integrity test protects a disposable repository
instead of inspecting registered user applications. The same validation,
non-execution, tamper, metrics and integrity contracts remain covered.

## Documentation map

Current normative contracts are TOOL_DESIGN, ROUTING, RESULT_CONTRACT,
EXECUTION_POLICY, EXECUTION_GATE, RUNTIME_EFFECTS, CONFINEMENT,
MACOS_CONFINEMENT, PLANNING, COMPOUND_EXECUTION, COMMIT and TESTFLIGHT under
`tools/natta/docs/`. Phase-labelled sections preserve design rationale and
historical domain evolution. Historical host narrative/results are excluded or
marked as source history. Current production planning uses `planner_provider_v3`
(status/context/diff/build/test/verify/commit/testflight); v1/v2 provider modules
remain controlled historical evaluation domains. Current behavior takes precedence
over historical phase scope. `docs/ROADMAP.md` contains future ideas only.

Two commit-evidence tests now pin sanitized public input bytes and preserve a
new synthetic failed evaluation result instead of depending on private historical
results. `public-controls-integrity.json` protects the exact published reproducible
controls. Historical stale-provider controls continue to fail closed.
