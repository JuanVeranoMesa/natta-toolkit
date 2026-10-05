# Explicit capability integration

Registration is a reviewed integration boundary. Arbitrary scripts are not
automatically discovered or treated as plugins. A fundamentally new capability
may require coordinated changes to:

1. A deterministic implementation with one explainable outcome.
2. `natta.py` CLI registration and direct dispatch.
3. `router_codex.py` authoritative IDs and frozen routing metadata; planner domain
   only if the capability is intentionally allowed in plans.
4. `parameters.py` contracts derived from registered CLI arguments, with local
   bounded resolution and validation.
5. `policy.py` capability type, effects and authorization declarations.
6. `natta.handler_bindings()` and semantic binding/parity validation.
7. `confinement.py`, runtime effects and any reviewed protection behavior.
   Unsupported confinement must fail closed; do not invent availability.
8. Structured result support where appropriate and tests for parity, rejected input,
   authorization, protected effects, failures and direct/semantic separation.

Composition belongs in workflows, not routing. Capability type, domain and
execution type are separate axes. Keep existing explicit mechanisms rather than
introducing a generic argument/result/plugin framework.

## Walkthrough: existing diff capability

`natta diff example` resolves a registered project and uses explicit Git argv,
disabling optional locks, external diff and text conversion. It reports change
summaries without reading untracked content. CLI `diff` registration is part of
the authoritative semantic universe. Routing proposes `diff`; local parameter
resolution supplies the project alias; policy declares inspection effects; the
static binding invokes the existing handler. Semantic execution requires the
validated inspection backend and before/after protected-state verification.

`natta route "Show changes in Example App"` only returns a proposed route.
`natta execute "Show changes in Example App"` enters the semantic gate and fails
closed if inspection confinement has not been validated locally. This is a compact
example of the real extension path, using existing project/result contracts.

A new project is configuration, not a new capability. A new adapter deliberately
implements `diagnose()` and `plan(operation, level, external_state)` and is added
to `adapters.ADAPTERS`, with tests. It must not evaluate registry command strings.
