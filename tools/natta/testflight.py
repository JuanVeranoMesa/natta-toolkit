"""Release archive + Apple-supported TestFlight delivery; never public release.

Raw Apple command output is private in-memory diagnostic input, never a report or
retained log. Existing execution-directory ownership and protected-state checks
are reused. Credentials are external references, not registry/planner data.
"""
from dataclasses import dataclass, field, replace
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import subprocess

import adapters
from execution import ExecutionDirectory, NattaError, assert_integrity, snapshot, bounded_text
import policy
import runtime_effects


class PrerequisiteError(Exception):
    pass


@dataclass(frozen=True)
class Authentication:
    mode: str
    flags: tuple[str, ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class Distribution:
    scheme: str
    bundle_id: str
    version: str
    build_number: str
    team: str
    base: tuple[str, ...]
    authentication: Authentication = field(repr=False)


@dataclass(frozen=True)
class TestFlightResult:
    status: str
    project: str
    stage: str | None = None
    error: str | None = None
    archive_succeeded: bool = False
    export_succeeded: bool = False
    upload_succeeded: bool = False
    execution_started: bool = False
    upload_started: bool = False
    bundle_id: str | None = None
    version: str | None = None
    build_number: str | None = None
    scheme: str | None = None
    authentication: str | None = None
    readiness: dict = field(default_factory=dict)
    effect_verification: runtime_effects.VerificationResult = field(default_factory=runtime_effects.VerificationResult)

    @property
    def exit_code(self):
        return 0 if self.status in ('ready', 'uploaded') else 1

    def as_dict(self):
        return {'status': self.status, 'project': self.project, 'stage': self.stage,
            'error': self.error, 'uploaded': self.upload_succeeded,
            'archive_succeeded': self.archive_succeeded, 'export_succeeded': self.export_succeeded,
            'upload_succeeded': self.upload_succeeded, 'upload_started': self.upload_started, 'execution_started': self.execution_started,
            'bundle_id': self.bundle_id, 'version': self.version, 'build_number': self.build_number,
            'scheme': self.scheme, 'configuration': 'Release', 'authentication': self.authentication,
            'archive_path': None, 'artifacts_retained': False,
            'external_service': 'app_store_connect', 'remote_readiness_verified': False,
            'processing': 'pending' if self.upload_succeeded else None,
            'submitted_for_review': False, 'released_publicly': False,
            'pushed': False, 'committed': False, 'readiness': dict(self.readiness), 'effect_verification': self.effect_verification.as_dict()}


def apple_run(argv, cwd, *, env=None, timeout=120, capture=True):
    """Never shell/verbose/log argv, environment or Apple's potentially sensitive output."""
    return subprocess.run(list(argv), cwd=cwd, capture_output=True, text=True,
                          env=env, timeout=120 if timeout is None else timeout)


def authentication(projects, *, environ=None, preferences=None):
    env = os.environ if environ is None else environ
    names = ('NATTA_ASC_KEY_PATH', 'NATTA_ASC_KEY_ID', 'NATTA_ASC_ISSUER_ID')
    values = tuple(env.get(n, '') for n in names)
    if any(values):
        path, key_id, issuer = values
        try:
            key = Path(path).expanduser()
            info = key.stat()
            if (not key.is_absolute() or key.is_symlink() or key.resolve() != key
                    or any(p.is_symlink() for p in key.parents)
                    or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or info.st_nlink != 1 or not 0 < info.st_size <= 65536
                    or key.suffix != '.p8' or not re.fullmatch(r'[A-Z0-9]{10}', key_id)
                    or not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', issuer)
                    or key.is_relative_to(Path(__file__).resolve().parent.parent.parent)
                    or any(key.is_relative_to(p.path) for p in projects)
                    or any((parent / '.git').exists() for parent in key.parents)):
                raise ValueError('Unsafe credential reference')
        except (OSError, ValueError):
            raise PrerequisiteError('api_key_configuration_invalid') from None
        return Authentication('app_store_connect_api_key',
            ('-authenticationKeyPath', str(key), '-authenticationKeyID', key_id,
             '-authenticationKeyIssuerID', issuer))
    preferences = preferences or Path.home() / 'Library/Preferences/com.apple.dt.Xcode.plist'
    try:
        accounts = plistlib.loads(Path(preferences).read_bytes()).get('DVTDeveloperAccountManagerAppleIDLists')
        # Local indication only, not proof of a current authenticated Apple session.
        def populated(value):
            if isinstance(value, (list, tuple)): return any(populated(v) for v in value)
            if isinstance(value, dict): return any(populated(v) for v in value.values())
            return isinstance(value, str) and bool(value.strip())
        if populated(accounts): return Authentication('xcode_account')
    except (OSError, ValueError, plistlib.InvalidFileException):
        pass
    raise PrerequisiteError('xcode_account_or_api_key_required')


def prerequisites(projects, project, state, runner=None, *, readiness=None):
    readiness = {} if readiness is None else readiness
    runner = apple_run if runner is None else runner
    if project.type != 'ios-xcode': raise PrerequisiteError('unsupported_adapter')
    if not project.path.is_dir(): raise PrerequisiteError('project_missing')
    if shutil.which('xcodebuild') is None: raise PrerequisiteError('xcodebuild_missing')
    adapter = adapters.resolve(project, runner)
    try:
        config = adapter.metadata()
    except (NattaError, OSError, ValueError):
        raise PrerequisiteError('container_scheme_or_configuration_missing') from None
    if 'project' in config and 'Release' not in adapter.metadata_names['configurations']:
        raise PrerequisiteError('release_configuration_missing')
    base = (*adapter.base, '-scheme', config['scheme'], '-configuration', 'Release',
        '-destination', 'generic/platform=iOS', '-derivedDataPath', str(state / 'DerivedData'),
        '-clonedSourcePackagesDirPath', str(state / 'Packages'), '-disableAutomaticPackageResolution')
    try:
        # No provisioning permission or network authentication flags in preflight.
        result = runner((*base, '-showBuildSettings', '-json'), project.path)
        if result.returncode: raise ValueError('Settings unavailable')
        records = json.loads(result.stdout)
        applications = [r['buildSettings'] for r in records
            if r['buildSettings'].get('PRODUCT_TYPE') == 'com.apple.product-type.application'
            and r['buildSettings'].get('SKIP_INSTALL') == 'NO']
        if len(applications) != 1: raise PrerequisiteError('one_archiveable_app_required')
        settings = applications[0]
        if settings.get('CONFIGURATION') != 'Release': raise PrerequisiteError('release_configuration_required')
        bundle, version, build, team = (settings.get(k, '') for k in
            ('PRODUCT_BUNDLE_IDENTIFIER', 'MARKETING_VERSION', 'CURRENT_PROJECT_VERSION', 'DEVELOPMENT_TEAM'))
        if (not re.fullmatch(r'[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+', bundle)
                or not re.fullmatch(r'\d+(?:\.\d+){0,2}', version)
                or not re.fullmatch(r'\d+(?:\.\d+){0,2}', build)):
            raise PrerequisiteError('bundle_version_or_build_missing')
        style = settings.get('CODE_SIGN_STYLE')
        readiness.update(team_configured=bool(re.fullmatch(r'[A-Z0-9]{10}', team)),
                         signing_style=style if style in ('Automatic', 'Manual') else 'unsupported')
        if not readiness['team_configured']:
            raise PrerequisiteError('development_team_required')
        if settings.get('CODE_SIGNING_ALLOWED', 'YES') != 'YES':
            raise PrerequisiteError('code_signing_disabled')
        if style not in ('Automatic', 'Manual'):
            raise PrerequisiteError('signing_style_required')
        # Keychain inspection is local evidence, not authority over Xcode-managed
        # signing. Cloud distribution signing need not have a local identity.
        try:
            identities = runner(('security', 'find-identity', '-v', '-p', 'codesigning'), project.path)
            matching = re.findall(r'"((?:Apple Development|Apple Distribution|iPhone Developer|iPhone Distribution):[^\n]*\('
                                  + re.escape(team) + r'\))"', identities.stdout)
            readiness['local_signing_identity'] = ('unknown' if identities.returncode else
                                                   'available' if matching else 'unavailable')
            readiness['local_distribution_identity'] = ('unknown' if identities.returncode else
                'available' if any(name.startswith(('Apple Distribution:', 'iPhone Distribution:')) for name in matching)
                else 'unavailable')
        except (OSError, subprocess.SubprocessError):
            readiness.update(local_signing_identity='unknown', local_distribution_identity='unknown')
        if style == 'Manual':
            if readiness['local_distribution_identity'] != 'available':
                raise PrerequisiteError('usable_team_signing_identity_required')
            # Preserve the existing automatic-only export contract; never silently
            # convert a manual project or fabricate distribution-profile mappings.
            raise PrerequisiteError('manual_signing_export_mapping_unsupported')
        try:
            auth = authentication(projects)
        except PrerequisiteError:
            readiness['authentication_ready'] = False
            raise
        readiness['authentication_ready'] = True
        help_result = runner(('xcodebuild', '-help'), project.path)
        required_options = ('app-store-connect', 'manageAppVersionAndBuildNumber', 'destination', '-allowProvisioningUpdates')
        if auth.mode == 'app_store_connect_api_key':
            required_options += ('-authenticationKeyPath', '-authenticationKeyID', '-authenticationKeyIssuerID')
        if help_result.returncode or not all(word in (help_result.stdout + help_result.stderr) for word in required_options):
            raise PrerequisiteError('unsupported_distribution_toolchain')
        readiness.update(xcode_managed_signing_eligible=True, local_archive_upload_ready=True)
        return Distribution(config['scheme'], bundle, version, build, team, tuple(base), auth)
    except PrerequisiteError: raise
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError):
        raise PrerequisiteError('archive_metadata_unavailable') from None


def archive_argv(distribution, state):
    return (*distribution.base, '-archivePath', str(state / 'App.xcarchive'),
            '-allowProvisioningUpdates', *distribution.authentication.flags, 'archive')


def export_options(distribution, destination):
    return {'method': 'app-store-connect', 'destination': destination,
            'signingStyle': 'automatic', 'teamID': distribution.team,
            'manageAppVersionAndBuildNumber': False, 'uploadSymbols': True}


def export_argv(distribution, state, destination):
    options = state / (destination + '-options.plist')
    with options.open('wb') as stream:
        plistlib.dump(export_options(distribution, destination), stream)
    options.chmod(0o600)
    return ('xcodebuild', '-exportArchive', '-archivePath', str(state / 'App.xcarchive'),
            '-exportOptionsPlist', str(options), '-exportPath', str(state / destination),
            '-allowProvisioningUpdates', *distribution.authentication.flags)


def validate_archive(distribution, state):
    archive = state / 'App.xcarchive'
    if archive.is_symlink(): raise PrerequisiteError('archive_invalid')
    info = plistlib.loads((archive / 'Info.plist').read_bytes())['ApplicationProperties']
    app_path = Path(info['ApplicationPath'])
    app = (archive / 'Products' / app_path).resolve()
    if not app.is_relative_to(archive.resolve()) or not app.is_dir() or app.suffix != '.app':
        raise PrerequisiteError('archive_invalid')
    bundle = plistlib.loads((app / 'Info.plist').read_bytes())
    if (bundle.get('CFBundleIdentifier'), bundle.get('CFBundleShortVersionString'), str(bundle.get('CFBundleVersion'))) != (
            distribution.bundle_id, distribution.version, distribution.build_number):
        raise PrerequisiteError('archive_identity_or_version_changed')
    if info.get('Team') != distribution.team:
        raise PrerequisiteError('archive_signing_team_changed')


def failure_code(stage, output):
    # Fixed diagnostics only; never forward server/tool text, key paths or tokens.
    if re.search(r'(90062|CFBundleShortVersionString.*(?:higher|previous|approved))', output, re.I):
        return 'version_conflict_update_explicitly'
    if re.search(r'(90189|bundle version.*(?:higher|previous)|CFBundleVersion.*(?:higher|previous)|redundant binary|already.*(?:uploaded|used))', output, re.I):
        return 'build_number_conflict_update_explicitly'
    if re.search(r'(authentication failed|unauthorized|invalid.*credential|session.*expired)', output, re.I):
        return 'apple_authentication_failed'
    if re.search(r'(provisioning profile|signing certificate|no profiles|no signing certificate|code ?sign.*(?:failed|error)|requires a development team|cloud.*sign.*(?:denied|permission))', output, re.I):
        return 'apple_signing_failed'
    return stage + '_failed'


def execute(projects, project, *, check=False, runner=None):
    runner = apple_run if runner is None else runner
    readiness = {'team_configured': None, 'signing_style': None,
                 'local_signing_identity': 'not_checked', 'local_distribution_identity': 'not_checked',
                 'xcode_managed_signing_eligible': False, 'authentication_ready': None,
                 'local_archive_upload_ready': False}
    result = TestFlightResult('prerequisites_failed', project.alias, 'prerequisites', readiness=readiness)
    before = baseline = None
    stage = 'prerequisites'
    try:
        before = runtime_effects.capture(project)
        baseline = snapshot(project.path)
        session = ExecutionDirectory(projects, project, 'testflight')
        try:
            distribution = prerequisites(projects, project, session.path, runner, readiness=readiness)
            assert_integrity(project.path, baseline, 'TestFlight prerequisites')
            result = replace(result, bundle_id=distribution.bundle_id, version=distribution.version,
                build_number=distribution.build_number, scheme=distribution.scheme,
                authentication=distribution.authentication.mode)
            if check:
                result = replace(result, status='ready', stage=None)
            else:
                env = {k:v for k,v in os.environ.items() if not k.startswith(('NATTA_ASC_', 'GIT_'))}
                env.update(TMPDIR=str(session.path), GIT_OPTIONAL_LOCKS='0', GIT_PAGER='cat')
                for stage in ('archive', 'export', 'upload'):
                    assert_integrity(project.path, baseline, 'before TestFlight ' + stage)
                    if stage != 'archive': validate_archive(distribution, session.path)
                    command = archive_argv(distribution, session.path) if stage == 'archive' else export_argv(distribution, session.path, stage)
                    result = replace(result, execution_started=True, upload_started=stage == 'upload' or result.upload_started)
                    completed = runner(command, project.path, env=env, timeout=3600)
                    if stage == 'upload' and completed.returncode == 0:
                        result = replace(result, upload_succeeded=True)
                    assert_integrity(project.path, baseline, 'TestFlight ' + stage)
                    if completed.returncode:
                        result = replace(result, status=stage + '_failed', stage=stage,
                            error=failure_code(stage, (completed.stdout or '') + (completed.stderr or '')))
                        break
                    if stage == 'archive': validate_archive(distribution, session.path)
                    if stage == 'export' and not any((session.path / 'export').glob('*.ipa')):
                        raise PrerequisiteError('export_package_missing')
                    result = replace(result, **{stage + '_succeeded': True})
                    if stage == 'upload': result = replace(result, status='uploaded', stage=None)
        finally:
            session.cleanup()  # Includes archive, options, output and Apple temporary files.
    except PrerequisiteError as exc:
        result = replace(result, status=stage + '_failed', stage=stage, error=str(exc))
    except NattaError:
        result = replace(result, status='verification_failed', stage='verification', error='protected_state_or_runtime_ownership_failed')
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        result = replace(result, status=stage + '_failed', stage=stage, error=stage + '_unavailable')
    if before is not None:
        try:
            after = runtime_effects.capture(project, before)
            verification = runtime_effects.compare(before, after, policy.CATALOG['testflight'].effects)
            if baseline is not None: assert_integrity(project.path, baseline, 'TestFlight completion')
        except Exception:
            verification = runtime_effects.unavailable('testflight_post_verification_failed')
        result = replace(result, effect_verification=verification)
        if verification.passed is not True:
            result = replace(result, status='verification_failed', stage='verification', error='protected_state_verification_failed')
    else:
        result = replace(result, status='verification_failed', stage='verification', error='pre_execution_verification_failed',
                         effect_verification=runtime_effects.unavailable('pre_execution_verification_failed'))
    return result


def render(result, project):
    lines = [f'TestFlight: {bounded_text(project.name)}']
    if result.status == 'ready':
        lines.append('PASS  Local prerequisites (remote authentication/app record/build number not verified)')
    else:
        for stage in ('archive', 'export', 'upload'):
            value = getattr(result, stage + '_succeeded')
            lines.append(('PASS' if value else 'FAIL' if result.stage == stage else 'SKIP') + '  ' + stage.capitalize())
    if result.readiness:
        ready = result.readiness
        def state(value):
            return 'yes' if value is True else 'no' if value is False else 'not checked'
        lines.extend(['Team configured: ' + state(ready.get('team_configured')),
                      'Signing style: ' + (ready.get('signing_style') or 'not checked'),
                      'Local team signing identity: ' + ready.get('local_signing_identity', 'not_checked'),
                      'Local distribution identity: ' + ready.get('local_distribution_identity', 'not_checked'),
                      'Xcode-managed signing eligible: ' + state(ready.get('xcode_managed_signing_eligible')),
                      'Authentication configured locally: ' + state(ready.get('authentication_ready')),
                      'Local archive/upload prerequisites ready: ' + state(ready.get('local_archive_upload_ready')),
                      'Remote signing/authentication/upload readiness: not verified'])
    if result.version: lines.extend(['Version: ' + result.version, 'Build: ' + result.build_number])
    if result.error: lines.append('Error: ' + result.error)
    if result.upload_succeeded:
        lines.append('Uploaded to App Store Connect for TestFlight. Processing may continue asynchronously.')
    lines.extend(['Not submitted for App Review.', 'Not released publicly.'])
    return '\n'.join(lines)


def doctor_checks(projects):
    checks = []
    for project in projects:
        if project.type != 'ios-xcode': continue
        result = execute(projects, project, check=True)
        checks.append((result.status == 'ready', f'{project.alias}: TestFlight local readiness: ' + (result.error or 'ready')))
    if checks: checks.append((True, 'TestFlight remote App Store Connect upload NOT verified; doctor never archives/uploads'))
    return checks
