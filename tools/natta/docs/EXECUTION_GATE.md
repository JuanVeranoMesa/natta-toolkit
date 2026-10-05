> Standalone v1: current contracts are preserved below. Phase-labelled sections
> record historical domains/rationale; private empirical artifacts are excluded.
> See [documentation map and evaluation provenance](../../../docs/EVALUATION.md).

# Single-capability semantic execution

Luna selects → Natta validates → deterministic resolver → deterministic policy →
invocation-local authorization → protected snapshot → revalidated bound handler →
post-snapshot/effect verification. Luna never authorizes
or executes. `natta route` remains permanently reporting-only. Bounded `natta do`
composes this gate after whole-plan authorization. Interactive terminal confirmation is supported.

```sh
natta execute [--json] "Show me what changed in Sample"
natta execute "Build Example for the simulator"                    # TTY: prompts
natta execute [--json] --confirm "Build Example for the simulator" # may build
```

The root `--registry PATH` option retains its usual position before the command.
Each semantic execution performs the same one Luna selection as route. No new
prompt/schema/descriptions/order/model, retries or provider fallbacks are introduced.
Local resolution, authorization, dispatch and deterministic handlers make no Luna
calls. Direct projects/status/context/diff/doctor/build/test/verify commands keep
their original behavior, bypassing semantic selection and this authorization gate.

## Authorization

The immutable policy catalog, not capability-name conditionals or request wording,
determines authorization. Automatic policy permits dispatch after all local gates.
Explicit policy requires an affirmative terminal response or `--confirm` on this invocation. Words such as "please",
"yes", "definitely run it" and "I authorize this" cannot satisfy that gate.
Authorization is never persisted. Forbidden policy cannot be overridden by any flag.
Neither confirmation nor automatic policy overrides unresolved parameters, unknown
capabilities, errors, denied effects or missing bindings.

## Bindings and revalidation

`natta.handler_bindings()` exposes an immutable mapping of ten known target IDs to
static local callables. Projects and doctor use their existing render/diagnostic
logic; status/context/diff share their existing inspection logic; build/test/verify
share their existing workflow logic; commit and TestFlight have dedicated bound handlers. Direct CLI and semantic execution use those
same implementations. There are no dynamically imported handlers, shell fragments,
model-produced argv or arbitrary flags.

The execute control parser is deliberately outside the `pages` target registrations
read by the frozen provider. Execute, route and Codex are not selectable execution
targets. Plan, do and confinement are also controls outside the target registrations.

Immediately before invoking a handler, the gate validates exact binding/catalog
coverage, reloads the authoritative registry, reads the actual parser schema and
recomputes policy using bounded parameters. Registered canonical project ID,
argument shape and level domain must remain valid. Recomputed policy must exactly
match the internal route policy, be eligible, contain no denied effects and have
its authorization requirement satisfied. Handlers receive fresh registry objects,
a canonical project object or null, and immutable validated arguments. Raw request
text has no path to a handler. External route JSON is not accepted as dispatch data.

Revalidation detects changes between resolution and dispatch; it is not a lock on
registry files or the filesystem. Normal direct workflow repository integrity
checks still apply. Trusted build phases/tests remain outside a filesystem/network
sandbox: declared effects do not prove or prevent tool misbehavior. No semantic path authorizes project writes or Git push. Only TestFlight permits
its reviewed Apple network/external-service effects. Only the reviewed explicit commit target permits Git index/commit
mutation, using its commit-specific verifier and postconditions. Other mutating
capabilities need separate implementation and policy design.

Doctor validates exact binding coverage against known CLI/policy IDs and callable
bindings. Its integrity check invokes neither routed handlers nor a provider.
The existing doctor diagnostic probes retain their direct-command behavior.

## Execution result

`semantic_execution.ExecutionResult` is immutable and separate from RouteResult.
JSON includes the route's capability/project/arguments/provider/resolution/policy
fields plus execution status, authorization, authorization_satisfied,
execution_started, handler_succeeded, execution_succeeded, effect_verification,
executed, result and error.

- executed is an alias of execution_started, not proof of success.
- execution_started becomes true immediately before the one bound handler call;
  it remains true if validation inside that handler or its work fails.
- execution_succeeded is null before invocation; after invocation it is true only
  for a valid aggregate handler exit zero AND required runtime verification pass,
  otherwise false. handler_succeeded records the handler outcome separately. Existing unavailable,
  skipped and passed-with-omissions check semantics remain distinct in checks.
- authorization_satisfied reports the gate result; it does not guarantee success.
- result is null before invocation. Afterwards it contains exit_code, bounded
  report (maximum 12,000 characters), report_truncated and structured workflow
  checks (name/status/exit/duration/bounded diagnostics/test identifiers).

Inspection has only its existing human renderer, so report is presentation data,
not a scraped decision/dispatch input. Workflow aggregation uses existing structured
Check/Result objects and exit codes. Raw tool output, commands and unlimited detail
are not exported by default. Human execution output adds a concise gate header
and the existing bounded capability report. JSON stdout contains exactly one JSON
object; handler stdout/stderr are captured in memory with a cap. No persistent
request/output logging is introduced.

Statuses: executed (handler exit zero and required verification passed),
effect_violation, verification_unavailable, execution_failed (nonzero or exception),
authorization_required, authorization_declined, unresolved, no_match, execution_denied, and existing
provider_error/routing_error/invalid_request. Denials exit 1; parser errors exit 2.
Invoked handlers preserve their aggregate exit code except CLI effect/verification
failures exit 1; result.exit_code still records the handler code. Handler exceptions/invalid
outcomes become execution_failed with exit 1 and bounded handler_error. Existing
NattaError/OSError diagnostics (including integrity failures) are sanitized and
bounded to 1,000 characters in the report; unexpected exception text and all
tracebacks are omitted; started=true and succeeded=false. Process interruption
can terminate the command before an envelope is printed; no result is fabricated.

Semantic execute deliberately does not accept --verbose/--log yet. Direct workflow
flags retain their existing independent streaming/retention behavior. No flags or
paths are extracted from natural language. See [host acceptance](../../../docs/EVALUATION.md).

Historical source evaluation decisions remain
capability-selection evidence only. They do not measure argument resolution,
authorization, dispatch or tool enforcement. No benchmark was expanded or altered.

See [runtime verification](RUNTIME_EFFECTS.md) for protected scope, failure precedence
and enforcement gaps. No automatic rollback is performed.

Semantic status/context/diff use a current host-validated macOS inspection
sandbox and mandatory runtime verification. Build/test/verify require explicit
authorization (terminal approval or --confirm) and mandatory protected-state snapshots, but no native confinement.
Xcode sandbox-exec research is deliberately deferred, not a production dependency.
Policy-denied effects are distinct from actively enforced effects: workflow
network, external services and unrelated filesystem writes are residual risks.
Route never executes; direct commands remain unchanged; bounded `natta do` composes the same validated dispatcher.
See [current confinement boundary](MACOS_CONFINEMENT.md).

## First reviewed Git mutation — commit

Commit is atomic, project-required and explicit, using one local static handler.
Direct syntax is `natta commit PROJECT [--message MESSAGE] [--json]`; semantic
execute requires explicit authorization and uses only the deterministic default message.
Route never executes. Commit has no native confinement requirement and uses
mandatory commit-aware runtime evidence; only index/HEAD mutation is permitted.
Working-tree/branch/repository changes fail, with no rollback. ExecutionResult
result.commit carries the immutable CommitResult, including truthful mutation
state after failure. Planner v2 may report commit intents, never execute them.
Historical provider domains remain separate; public definitions have sanitized
project names/control hashes and private results are excluded. See [COMMIT.md](COMMIT.md).

## Interactive authorization

Human-readable execute prompts only when **both sys.stdin.isatty() and
sys.stdout.isatty() are true**, explicit authorization is required, and every
local eligibility/policy/binding check has passed. No timing or environment-variable
heuristics are used. JSON mode never supplies an authorization callback, even on a
terminal. Redirected stdin or stdout does not prompt. --confirm is invocation-local
preauthorization: it skips the prompt in all modes, still subject to every safety gate.

The display contains only the frozen validated capability, local project display
name and bounded arguments (for example verify Sample App level=2). It does not
contain raw request/provider prose or reasoning. Continue? [y/N] accepts only y
or yes, case-insensitively with surrounding whitespace stripped. Enter, n/no,
arbitrary text, EOF and unavailable input decline. Ctrl-C interrupts before any
handler; it may end without a result envelope, following the CLI's interruption behavior.

The same frozen RouteResult and Project are offered to the callback and retained
for dispatch; there is no reroute, second resolution or second model request.
After a yes, the gate reloads local registry data, validates schema/catalog/bindings,
recomputes policy and compares exact project/schema/policy plus bound-handler
identity. Changed targets fail closed with authorization_target_changed. This
revalidation is not a filesystem lock; runtime snapshots remain mandatory.

AuthorizationRequired means approval was not collected (machine mode/non-TTY).
AuthorizationDeclined means approval was offered and not affirmative. Their actual
status IDs are authorization_required and authorization_declined; both exit1,
executed=false, execution_started=false, authorization_satisfied=false and
execution_succeeded=null. Neither takes runtime snapshots or invokes handlers.
No_match/unresolved/forbidden/error routes never prompt. Automatic inspection does
not prompt and still requires native inspection confinement plus runtime verification.

The small authorization.request_approval helper accepts locally rendered action
lines and returns true/false or null for unavailable prompting. It has no provider,
resolver, policy or execution dependency. The bounded do caller reuses this
input mechanism for whole-plan authorization before dispatching any step. Route and plan stay
reporting-only. Direct build/test/verify/commit commands remain noninteractive and
use no Luna requests. No request/approval logging or persisted authorization exists.

Current inspection requires matching host/profile/source identity and fresh
local validation when that identity changes. The public manifest carries no
historical installation authority. See [evaluation provenance](../../../docs/EVALUATION.md).
