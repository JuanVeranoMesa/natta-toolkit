> Standalone v1: current contracts are preserved below. Phase-labelled sections
> record historical domains/rationale; private empirical artifacts are excluded.
> See [documentation map and evaluation provenance](../../../docs/EVALUATION.md).

# TestFlight: archive + upload a beta build

```sh
natta testflight example --check
natta testflight example
natta testflight sample
natta testflight example --confirm --json
```

The authoritative registry selects the project, Xcode container and scheme.
Arbitrary project paths and shell/Git options are not accepted. Future registered
`ios-xcode` projects use the same implementation. There is no `distribute` command.

**testflight** archives and uploads a build to App Store Connect's TestFlight
pipeline. **distribute** will be a separate future public App Store publication
workflow. TestFlight never submits App Review, selects a public release build,
changes listing/release settings, adds testers/groups, commits or pushes.

## Authorization

Direct uploads require one terminal y/yes approval or invocation-local --confirm.
The summary identifies the canonical project, registered scheme and Release
configuration, external Apple build record, Xcode-managed signing/provisioning,
and the no-review/no-public-release boundary. JSON/non-TTY never prompt and need
--confirm for an upload. --check is noninteractive local inspection only.

Semantic execute uses the same explicit policy and dispatcher. Compound do asks
once for the complete frozen plan before step 1, including the TestFlight upload
consequence. The handler never prompts again. Earlier build/test/commit failures
prevent TestFlight from starting. Existing direct capabilities retain their UX.

## Local preflight and credentials

--check verifies the protected registered Git root, ios-xcode adapter, container,
scheme, Release configuration, exactly one installable application target, literal
bundle/version/build values, signing style and a development team, local signing
identity availability, local authentication configuration, and supported
xcodebuild distribution options. It reads Xcode metadata and keychain identity
availability; it never builds, archives, exports, uploads or enables provisioning
updates. DerivedData/package paths are external and automatic package resolution
is disabled. Normal doctor runs these same local checks as optional readiness
information, without uploading or probing App Store Connect online.

V1 supports **automatic Xcode-managed signing**. A valid DEVELOPMENT_TEAM,
CODE_SIGN_STYLE=Automatic and enabled code signing are required. Missing local
Development/Distribution identities are informational, not a refusal: Xcode can
manage provisioning and cloud distribution signing after authorization. A failed
keychain probe is reported as unknown, never fabricated as an available identity.
--check reports team configuration, signing style, local team/distribution identity
availability, managed-signing eligibility, local authentication and local
archive/upload readiness separately. Eligibility is structural, not proof of
remote permissions or successful archive signing.

Manual signing fails locally if a usable team distribution identity is absent.
Even with an identity, manual export/profile mappings remain unsupported and are
reported as manual_signing_export_mapping_unsupported before archive. Natta never
silently switches manual projects to automatic signing or invents signing assets.
Existing Xcode accounts are used
when no API-key environment configuration is provided. Local account preferences
are an indication only: --check cannot prove current remote login, account role,
cloud-signing permissions, agreements, App Store app-record existence, or the next
acceptable build number. The matching app record/bundle ID must already exist in
App Store Connect. Appropriate Apple Developer/App Store Connect permissions,
keychain access, supported Xcode and distribution entitlements are prerequisites.

Alternatively set the following in your local process environment, outside any
repository, registry, request, log or evaluation artifact:

- NATTA_ASC_KEY_PATH: absolute external path to an existing .p8 API private key.
- NATTA_ASC_KEY_ID: existing 10-character key identifier.
- NATTA_ASC_ISSUER_ID: existing issuer UUID.

All three are required together. The key must be an ordinary, singly linked,
user-owned file with no group/other permissions (normally chmod 600), outside the
toolkit checkout, all registered projects and any Git repository. Symlinks are
rejected. Natta never reads/copies/prints the key contents, creates API credentials,
stores passwords, or emits key paths/IDs/issuer IDs in result JSON. Authentication
references go only to documented xcodebuild flags. Xcode manages existing account
credentials/keychain access. Complete missing account/certificate setup yourself;
Natta does not silently install or repair it. Metadata and tools cannot prove
remote credentials are valid before an authorized upload.

After approval, `-allowProvisioningUpdates` permits Xcode's supported managed
provisioning and signing, including necessary profiles/certificate updates. This
is external/runtime state explicitly included in the authorization consequence;
it is never enabled by --check or doctor. No API key/password is created by Natta.

## Apple commands and version handling

Natta reuses IOSXcode metadata and the canonical registry container/scheme.
All commands are argv lists; no shell is involved.

1. `xcodebuild ... -configuration Release -destination generic/platform=iOS
   -derivedDataPath ... -clonedSourcePackagesDirPath ... -archivePath ... archive`.
2. `xcodebuild -exportArchive` with method=app-store-connect, destination=export,
   signingStyle=automatic, teamID, uploadSymbols=true and
   **manageAppVersionAndBuildNumber=false**, producing a local .ipa.
3. `xcodebuild -exportArchive` of the same archive with destination=upload.
   This final command signs/packages for delivery and uploads using Xcode's
   supported account/API-key mechanism. The separate local export ensures its
   failure stops before the network-facing delivery command.

Archive app identity, signing team, marketing version and build number must match
preflight. Export/upload options contain no credentials. Source version/build
settings are never changed, nor is automatic archive build-number management
allowed. Apple's duplicate-build/version rejection produces a bounded explicit
conflict code, requiring a deliberate separate project version/build update.
No retry, auto-bump or rollback occurs.

Successful archive/export alone never means uploaded. uploaded/upload_succeeded
means the final Apple delivery command returned success. App Store Connect
processing is asynchronous and may later fail. Natta does not poll remote
processing or claim that a build is immediately available to testers. upload_started
separately records whether the delivery command started; a failed/timeout command
cannot prove no external build record exists. An accepted upload remains reported
truthfully even if later protected-state verification fails.

Apple documents [cloud signing and xcodebuild authentication](https://developer.apple.com/videos/play/wwdc2021/10204/)
and [upload roles and asynchronous processing](https://developer.apple.com/help/app-store-connect/manage-builds/upload-builds/).
The installed Xcode 27 `xcodebuild -help` is the exact option reference for
app-store-connect, destination, authentication flags and disabling version management.

## Runtime, effects and results

ExecutionDirectory owns a private external temporary root for archives,
DerivedData, packages, generated export options and output. Its existing ownership
checks govern cleanup; material is removed on normal success/failure. Results
report archive_path=null/artifacts_retained=false, rather than a deleted path.
No verbose/log flags are exposed. Apple command output stays in memory and is
never forwarded to human output, JSON or retained Natta logs. Errors are fixed
stage codes; credential paths, raw stderr and full command environments are not
reported. This sacrifices detailed Apple diagnostics in v1 to protect credentials.

Protected snapshots and the existing integrity assertions wrap discovery and
every stage. The existing outer semantic dispatcher also performs mandatory
runtime effect verification. Unexpected project/Git mutation fails overall,
prevents subsequent stages and is never undone. Trusted Xcode build scripts are
not sandboxed; effect observation cannot certify absence of transient restored
writes or unrelated external effects.

Explicit workflow effects are local_read, project_read, git_read, local_process,
temporary_write, persistent_runtime_write, network and external_service_mutation.
There is no existing signing/keychain effect enum; those writes are runtime state.
The reviewed network/external-service exception is confined to explicit TestFlight
policy. Other capabilities cannot acquire it. Project writes, index writes,
commits and pushes remain denied. TestFlight has no inspection sandbox requirement.

TestFlightResult exports status, project, stage/error, archive/export/upload
success flags, upload_started, execution_started, bundle_id, version, build_number,
scheme, Release configuration, auth **mode only**, local readiness fields, external_service,
processing=pending after acceptance, and submitted_for_review/released_publicly/
pushed/committed=false. It retains runtime verification. Failures identify
prerequisites/archive/export/upload/verification. Direct JSON includes the shared
execution envelope; semantic/compound JSON includes the complete TestFlightResult
under result.testflight. --check ready means local prerequisites only, never
remote readiness verified.

## Semantic extension and frozen evaluation

```sh
natta route "Upload Example to TestFlight"
natta execute "Upload Example to TestFlight"
natta plan "Build Example, test it, commit it, then upload it to TestFlight"
natta do "Build Example, test it, commit it, then upload it to TestFlight"
```

One router/planner request still suffices; no per-step provider calls exist.
TestFlight is added through additive routing descriptions and planner-v3 controls.
Historical Phase 4D, planner-v1, commit routing and planner-v2 descriptions,
provider domains remain separate; public corpora/control hashes are sanitized
and private results are excluded. The counted evaluation runner accepts
explicit provider domains so old evaluations continue using their frozen inputs.

The new frozen routing corpus has **12** cases; planner corpus **16**. They cover
both apps, aliases/upload/beta wording, unresolved targets, build/upload,
build/test/commit/upload, multiple projects, over-four bounds, public publication
and adversarial shell/credential requests. Before real calls, acceptance is
predeclared: **100% exact results**, zero provider failures, 100% unsupported and
adversarial rejection, zero accepted unknown capabilities/projects. The production
local public-release veto also prevents model-proposed safe prefixes from hiding
explicit publish/distribute/review/public-release requests. It is conservative;
phrasing that mentions public publication may be rejected even in a disclaimer.

No real evaluation has been run from Codex. These opt-in host commands perform
12 and 16 real Luna calls, never handlers/uploads, and exclusively create private
new artifacts (existing output paths refuse):

```sh
cd tools/natta
python3 -B testflight_evaluation.py routing --allow-external-requests 12 \
  --output "$PWD/evaluation/testflight-routing-luna-v1.json"
python3 -B testflight_evaluation.py planner --allow-external-requests 16 \
  --output "$PWD/evaluation/testflight-planner-luna-v1.json"
```

## Host acceptance: preflight first, then deliberate upload

Historical initial preflight required a local team identity even for automatic
signing, stopping both apps with usable_team_signing_identity_required. That
requirement was overly strict and is superseded by the managed-signing readiness
model above. No historical archive/upload acceptance is implied. Local --check
cannot verify cloud-signing rights, remote authentication or build-number availability.

From a normal terminal run the local-only preflight first:

```sh
natta testflight example --check
```

If Example is not ready, complete its signing/account prerequisites deliberately,
or inspect Sample with `natta testflight sample --check`. Do not upload until the chosen
app reports local readiness and you know its current build number is new for Apple.
Then run **one** real upload, review the displayed authorization summary and type y:

```sh
natta testflight example
```

Use `natta testflight sample` instead only if Sample is the ready/intended app. Expect
PASS Archive/Export/Upload, verified protected state, no commit/push, and explicit
no-App-Review/no-public-release output. Success is accepted delivery with pending
processing, not public release. No real uploads were run during development.

Future distribute owns public App Review/release/listing workflows with separate
policy/authorization and evidence. Manual-signing mappings, credential setup,
remote preflight/processing polling, tester/group management, persistent diagnostic
artifacts and explicit version/build mutation remain separate future work.

## Implementation file map

Added:

- testflight.py — bounded product workflow and structured result.
- planner_provider_v3.py — additive provider controls, max4 unchanged.
- testflight_semantics.py — conservative public-release veto.
- testflight_evaluation.py — frozen extension entry point using the existing runner.
- config/routing-testflight-v1.json — additive description/policy.
- evaluation/testflight-routing-v1-corpus.json and testflight-routing-v1-controls.json.
- evaluation/testflight-planner-v1-corpus.json and testflight-planner-v1-controls.json.
- tests/test_testflight.py — mocked Apple/provider tests, disposable repositories.
- docs/TESTFLIGHT.md — product contract, credentials and staged acceptance.

Updated:

- natta.py, adapters.py, authorization.py — first-class CLI/handler, metadata reuse and approval text.
- policy.py, confinement.py, semantic_execution.py — scoped effects and existing dispatch/result integration.
- compound_execution.py — frozen upload consequence and result presentation.
- router_codex.py, routing.py, planner_provider.py, planning.py — additive semantic domain with historical controls preserved.
- commit_evaluation.py — reusable evaluation runner hooks; historical default domains retained.
- macos_confinement.py, inspection_compatibility.json — include the new doctor dependency in exact fingerprints and start with no inherited inspection compatibility.
- tests/test_codex_evaluation.py, test_route.py, test_planning.py, test_commits.py, test_compound_execution.py — distinguish new production and frozen historical domains.
- README.md, AGENTS.md and docs/TOOL_DESIGN.md, ROUTING.md, RESULT_CONTRACT.md, EXECUTION_POLICY.md, PLANNING.md, COMPOUND_EXECUTION.md — current product boundaries.

Historical initial implementation validation: 436 total tests, including 40 new TestFlight tests, pass. Providers and
Apple archive/export/upload commands are mocked. All 28 pre-existing evaluation/
config artifacts were checked by SHA-256 and remain byte-identical. Existing
provider-control tests also verify historical prompt/description/schema parity.
No real Luna calls, app builds/tests/commits, archives/uploads, harness commits or
pushes were performed. Local metadata/signing checks ran as described above.
