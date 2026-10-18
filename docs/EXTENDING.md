# Adding a capability: contributor checklist

Registration is a reviewed integration boundary. Arbitrary scripts are never
discovered or treated as plugins, and nothing here is generated automatically.
Follow the steps in order; each names the exact file and symbol to change. All
paths are relative to `tools/natta/`.

The mechanical check for steps 1–10 is:

```sh
cd tools/natta
python3 -B capability_integration.py          # names the first layer that disagrees
python3 -B -m unittest discover -s tests -v   # full suite
```

`capability_integration.validate()` (also a core `doctor` row) compares the
catalog with the parser, direct dispatch, routing descriptions and their pins,
handler bindings, policy and effects, parameter contracts, confinement sets and
the current planner revision. It understands intentional categories: a
direct-only command needs no semantic layers, a routable capability need not be
planner-eligible, and an `unvalidated` capability has no native mode.

Terms used below:

- **command**: a CLI page (`natta <command>`), declared in `capabilities.COMMANDS`.
- **capability**: a command that is also a member of the bounded semantic
  universe (routing, policy, handler binding), declared in `capabilities.CAPABILITIES`.
- **direct-only command**: a command that is not a capability (for example `codex`,
  `route`, `execute`, `plan`, `do`, `confinement`). It needs only steps 1, 2 and 7.

## 1. Declare identity — `capabilities.py`

- Add a `Command(summary, description, example, project)` to `COMMANDS`.
  `project` is `'required'`, `'optional'` or `'none'` and creates the `PROJECT`
  positional. The parser (`natta.make_parser`) builds every page from this table;
  add command-specific options there, next to the existing ones.
- For a capability, **append** a `Capability(confinement, planner, direct)` to
  `CAPABILITIES`. Order is the routing candidate order: appending preserves the
  evaluated order of existing capabilities (a test checks this).
- `capabilities.py` is plain data. Do not import handlers, providers or the
  registry from it. Nothing parses `natta.py` source to discover IDs.

## 2. Write the deterministic handler — `natta.py` (or a focused module)

- Signature: `handle_x(projects, project, arguments) -> semantic_execution.HandlerOutcome`.
  `projects` is the freshly loaded registry tuple, `project` the canonical
  `Project` (or `None` when no project is required), `arguments` an immutable
  tuple of validated `(name, int)` pairs. The raw request never reaches it.
- One explainable outcome. No model or provider calls, no `shell=True`,
  `os.system`, `eval`/`exec`, or command text from the registry or the model:
  build argv tuples in code. Git calls use `natta.git()` (fixed safe argv), or
  pass `execution.git_environment()` (no ambient `GIT_*`) and
  `-c core.fsmonitor=false` themselves.
- Expected failures raise `NattaError` with a bounded message; never "fix",
  retry or roll back. Workflows that run tools use `ExecutionDirectory` for
  external temporary state and `execution.execute()` for checks.
- Also add the direct entry point: a `run_x(args, parser)` function registered in
  `natta.DIRECT_HANDLERS` (omit only for a deliberately semantic-only capability,
  `Capability(direct=False)`, whose direct invocation then fails closed).
  Reuse the same handler implementation for direct and semantic paths.

## 3. Parameters and schema — derived from the parser

- Parameter contracts come from the real argparse registration through
  `parameters.schema_for(parser, capability)`. Only a required `project` and a
  required integer `--level` with integer `choices` are supported; any other
  required argument makes `schema_for` refuse, so the capability is not routable.
- `parameters.resolve` resolves the project and level locally from the request
  (`project_matches`, `explicit_levels`). A new parameter kind is a deliberate
  change to `parameters.py`, `policy.evaluate`, `planning.validate` and their
  tests, not a generic framework.

## 4. Policy — `policy.py`

- Add a `CapabilityPolicy(capability_type, authorization, effects, level_effects)`
  to `policy.CATALOG`. `capability_type` is atomic, workflow or interface.
- `validate_catalog` enforces reviewed exceptions: only `commit` may declare
  `GIT_INDEX_WRITE`/`GIT_COMMIT`, only `testflight` may declare `NETWORK`/
  `EXTERNAL_SERVICE_MUTATION`, and `FORBIDDEN_EFFECTS` cannot be authorized.
  Granting a new capability any of these is a separate security review that
  changes those rules and their tests explicitly.

## 5. Effects — conservative upper bound

- Declare every effect the handler may have across all adapters (`Effect.*`),
  including `level_effects` when a level widens them. Effects are not an
  execution plan or an availability claim; they bound what is permitted.
- `runtime_effects` observes `PROJECT_WRITE`, `GIT_INDEX_WRITE` and `GIT_COMMIT`
  in protected snapshots; an observed effect outside the declaration is a
  violation and the result fails, even if the handler succeeded.

## 6. Authorization

- `AUTOMATIC` only for read-only inspection whose effects are local reads.
  Anything that builds, writes, mutates Git or uses the network is `EXPLICIT`.
- If approval needs to state a consequence, add it to
  `authorization.action_notice` (single action) and
  `compound_execution.authorization_notice` (plans), as commit and TestFlight do.
- Explicit actions are approved in a terminal after routing/planning, or
  preauthorized with `execute --confirm --expect CAPABILITY`. Never add a path
  where an approval given before routing covers whatever the model selects.

## 7. Handler binding — `natta.handler_bindings()`

- Add the static callable to the `MappingProxyType` in `handler_bindings()`
  (use `functools.partial` for shared implementations). Bindings must equal the
  capability set exactly; selection IDs are never imported or turned into argv.

## 8. Semantic routing eligibility — reviewed descriptions

Every catalog capability is routable. Routing descriptions are frozen inputs:

- Create `config/routing-<capability>-v1.json` with exactly
  `schema_version`, `capability` and `description` (plus an optional
  `routing_description` or `policy` string, as the commit and TestFlight files do).
- Append `(capability, file, schema_version, extra_fields)` to
  `router_codex.EXTENSIONS`. Extensions are cumulative and strictly validated.
- Pin the new file's SHA-256 in `evaluation/public-controls-integrity.json`.
- Do **not** edit `config/routing.json`, existing extension files, or the
  historical `ROUTING_IDS`/`PRODUCTION_IDS`/`TOOLKIT_IDS` orders; production
  uses `router_codex.current_ids()` from the catalog.

## 9. Planning eligibility — `planner_provider.py`

- Only capabilities that require a project may be `planner=True`.
- Planner revisions are entries, not modules. Add one `Revision(version,
  allowed, policy)` with a new version string, `allowed` equal to the previous
  revision's IDs plus the new capability, and the previous policy plus additive
  text; then point `CURRENT` at it. Never edit an existing revision: its version,
  allowed IDs and policy text are hashed by frozen controls.
- Add a frozen corpus and controls for the new revision under `evaluation/`
  (see `testflight_evaluation.py` for the smallest example) and pin them.
- Plan steps are reconciled with the goal: a step's project must be one the goal
  names and a verify level one it states (`planning.reconcile`).

## 10. Confinement decision

Choose one classification in `Capability.confinement` and the matching set in
`confinement.py`; the validator requires them to agree:

- **`inspection` (confined)**: read-only work that runs inside the validated macOS
  sandbox worker. Add the ID to `confinement.INSPECTION_TARGETS` (the worker and
  `macos_confinement.mode_for` accept only that set). Because confinement-relevant
  sources change, the inspection fingerprint changes (step 13) and hosts must
  re-run `natta confinement validate`.
- **`workflow` (observed)**: explicit authorization plus pre/post protected-state
  observation, no OS sandbox (build/test/verify/testflight). Add the ID to
  `confinement.WORKFLOW_TARGETS`. This is a deliberate exemption from native
  confinement; it is never inferred from effects or type.
- **`commit`**: reviewed Git mutation with commit-aware evidence. A second
  commit-like capability needs its own review.
- **`unvalidated`**: no validated native mode; semantic execution fails closed and
  the direct command remains available (projects, doctor).

## 11. Result and error contracts

- Handlers return `HandlerOutcome(exit_code, checks, ...)`. Structured product
  results (like `CommitResult`, `TestFlightResult`) need explicit fields on
  `HandlerOutcome` and `ExecutionResult.as_dict()`; do not smuggle them in text.
- Error codes are stable lowercase snake_case identifiers; messages are bounded
  (`execution.bounded_text`) and never echo provider prose or configured commands.
- JSON commands keep one object on stdout with a nonzero exit on failure.
  Document the envelope in `docs/RESULT_CONTRACT.md`.

## 12. Tests

Add, at minimum: handler unit tests in disposable repositories with mocked
external tools; parity (`tests/test_capability_integration.py` must still pass);
policy and effect tests; authorization tests (explicit route with a matching
`--expect`, mismatch refused before the handler, JSON/non-TTY never prompting);
confinement classification; planner gold plans when planner-eligible; direct
command behavior. Never call real providers, Apple services or real projects.
Update the test count in the README (`tests/test_public_docs.py` checks it).

## 13. Evaluation evidence and integrity pins

Pins exist so that evidence keeps meaning what it meant when it was recorded:

- `evaluation/public-controls-integrity.json` pins routing description files and
  frozen evaluation corpora/controls. Evaluation controls also hash the exact
  provider inputs they measured (routing workspace, planner policy/schema). If a
  pinned input changed silently, an old accuracy result would be presented as
  evidence for different inputs.
- `inspection_compatibility.json` pins the source fingerprint of the files in
  `macos_confinement.FILES`. A host's inspection validation record is accepted
  only for that exact implementation (or an audited earlier one listed in
  `previous_implementations`).

When to change them:

- **Add** a pin for every new routing/evaluation file. Adding is normal.
- **Never change** an existing pin to make a test pass. Changing pinned inputs
  means the old evidence no longer applies: create new versioned files/controls
  (as `routing-commit-v2.json` and the planner revisions do) instead.
- When `macos_confinement.FILES` sources change, set `current_implementation` to
  the new fingerprint. List a previous fingerprint only if it was published and
  the inspection behavior it validated is unchanged; never list unreleased
  intermediate fingerprints.

## 14. Documentation and help

- Help text lives in the `Command` entry (step 1); keep it free of internal
  project names and phase jargon.
- Update the README capability table, the relevant contract under
  `tools/natta/docs/`, and this checklist if the process itself changes.

## Walkthrough: the existing `diff` capability

`diff` is declared in `COMMANDS` with `project='required'` and in `CAPABILITIES` as
`Capability('inspection', planner=True)`. `handle_inspection('diff', ...)` uses
explicit Git argv (no external diff, text conversion or fsmonitor). Its policy is
automatic `PROJECT_INSPECTION`; `handler_bindings()` maps it via `partial`;
`DIRECT_HANDLERS` maps it to `run_inspection`. Routing describes it in
`config/routing.json`; the planner allows it from phase8a-v1 onwards; it is in
`confinement.INSPECTION_TARGETS`, so semantic execution requires validated
inspection confinement plus protected-state verification.

A new project is configuration, not a capability. A new adapter implements
`diagnose()` and `plan(operation, level, external_state)`, is added to
`adapters.ADAPTERS` (the registry rejects unknown adapter types at load), and
never evaluates registry command strings.

## Not yet provided

There is no capability scaffold/helper command and no user-selectable model
configuration; both are future work built on this checklist.
