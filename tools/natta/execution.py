"""Structured, shell-free execution; no persistent run history."""
from dataclasses import dataclass
import os
import hashlib
import codecs
from datetime import datetime
import json
import re
import shutil
import tempfile
import uuid
from pathlib import Path
import shlex
import subprocess
import time
import threading


class NattaError(Exception):
    pass


class ExternalFilterConfigured(NattaError):
    """A configured clean/process filter applies to repository paths."""
    code = 'external_filter_configured'

    def __init__(self):
        super().__init__('A Git clean/process filter applies to repository files; Natta '
                         'refuses to execute it as part of protected verification')


class SubmoduleUnsupported(NattaError):
    """The index contains a gitlink; protected verification does not support submodules."""
    code = 'submodule_unsupported'

    def __init__(self):
        super().__init__('Protected/workflow verification does not support repositories '
                         'containing Git submodules')


PROTECTED_REFUSALS = (ExternalFilterConfigured, SubmoduleUnsupported)


def refusal_message(code):
    """Fixed explanation for a protected-verification refusal code, else None."""
    return next((str(kind()) for kind in PROTECTED_REFUSALS if kind.code == code), None)


def git_environment():
    """The one Git child environment: ordinary process state, no ambient Git.

    GIT_* can select another repository, index, object store or configuration
    (GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE, GIT_CONFIG_*, ...). Natta names the
    repository with -C, so none are inherited; only fixed Natta controls apply.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0', GIT_PAGER='cat')
    return env


def child_environment(command):
    """Workflow child environment; Git children use git_environment()."""
    if command and Path(command[0]).name == 'git':
        return git_environment()
    return {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_PAGER": "cat"}


@dataclass(frozen=True)
class Check:
    name: str
    command: tuple[str, ...] = ()
    status: str = ""
    reason: str = ""
    optional: bool = False


@dataclass(frozen=True)
class Result:
    check: Check
    status: str
    exit_code: int
    duration: float
    detail: str = ""
    detail_lines: tuple[str, ...] = ()
    failing_tests: tuple[str, ...] = ()


def run(command, cwd, capture=False, timeout=None):
    return subprocess.run(list(command), cwd=cwd, capture_output=capture,
                          text=True, timeout=timeout, env=child_environment(command))


def query(command, cwd, runner=run):
    try:
        process = runner(command, cwd, capture=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NattaError(f"{shlex.join(command)}: {exc}") from exc
    if process.returncode:
        detail = bounded_text((process.stderr or process.stdout or "").strip())
        raise NattaError(f"{shlex.join(command)} exited {process.returncode}:\n{detail}")
    return process.stdout


def snapshot(cwd, *, protected=False, baseline=None):
    """Legacy content/Git guard; protected=True selects bounded Git evidence."""
    if protected:
        return protected_snapshot(cwd, baseline=baseline)
    base = ("git", "--no-optional-locks", "-C", str(cwd))
    env = git_environment()
    refuse_applied_filters(cwd)  # git status would run an applied clean/process filter.
    def read(*args):
        return subprocess.check_output((*base, *args), env=env)
    paths = set(read("ls-files", "--cached", "--others", "-z").split(b"\0")) - {b""}
    hashes = {}
    for raw in sorted(paths):
        path = Path(cwd) / os.fsdecode(raw)
        content = os.fsencode(os.readlink(path)) if path.is_symlink() else path.read_bytes() if path.is_file() else None
        hashes[raw] = hashlib.sha256(content).hexdigest() if content is not None else None
    gitdir = Path(os.fsdecode(read("rev-parse", "--absolute-git-dir")).strip())
    index = gitdir / "index"
    commit = subprocess.run((*base, "rev-parse", "--verify", "HEAD"), capture_output=True, env=env)
    return {"commit": (commit.returncode, commit.stdout), "files": hashes, "status": read("status", "--porcelain=v1", "-z", "--untracked-files=all"),
            "head": read("symbolic-ref", "-q", "HEAD") if (gitdir / "HEAD").read_text().startswith("ref:") else (gitdir / "HEAD").read_bytes(),
            "index": index.read_bytes() if index.exists() else None}


@dataclass(frozen=True)
class ProtectedSnapshot:
    repository: str
    git_directory: str
    baseline: str
    commit: bytes
    branch: bytes
    index: str | None
    project_state: str
    untracked: tuple[bytes, ...]
    index_entries: str = ''
    worktree_contents: tuple[tuple[bytes, str | None], ...] | None = None


def refuse_applied_filters(cwd):
    """Raise SubmoduleUnsupported for any index gitlink, else ExternalFilterConfigured
    if a configured clean/process filter applies.

    Never runs an external filter: configured `filter.<driver>.clean/process`
    names (any config scope) are matched against `check-attr` for tracked and
    nonignored untracked paths. check-attr resolves .gitattributes,
    info/attributes, global/system attributes and macros; unset/unspecified/
    valueless filters select no driver. Configured but unused drivers are allowed.
    Submodules are never inspected: a submodule's own config is invisible here
    and diff/status would recurse into it, so any gitlink (mode 160000) is
    refused first, from the index alone, before any other Git query.
    """
    env = git_environment()
    base = ('git', '--no-optional-locks', '-c', 'core.fsmonitor=false', '-c', 'core.untrackedCache=false', '-C', str(cwd))
    def bounded(args, payload=b'', optional=False):
        process = subprocess.run((*base, *args), cwd=Path(cwd), input=payload,
                                 capture_output=True, timeout=30, env=env)
        if (process.returncode and not (optional and process.returncode == 1)
                or len(process.stdout) > 64 * 1024 * 1024):
            raise NattaError('Protected Git snapshot unavailable')
        return process.stdout
    # Reads the index only (no worktree refresh, no recursion), so no filter runs.
    if any(entry.startswith(b'160000 ') for entry in bounded(('ls-files', '--stage', '-z')).split(b'\0')):
        raise SubmoduleUnsupported()
    configured = {key[len(b'filter.'):key.rindex(b'.')] for key in
                  (record.split(b'\n', 1)[0] for record in bounded(
                      ('config', '-z', '--get-regexp', r'^filter\..*\.(clean|process)$'),
                      optional=True).split(b'\0') if record)}
    if not configured:
        return
    paths = bounded(('ls-files', '--cached', '--others', '--exclude-standard', '-z'))
    if len(paths) > 16 * 1024 * 1024:
        raise NattaError('Protected snapshot exceeds bounded evidence limit')
    if not paths:
        return
    fields = bounded(('check-attr', '-z', '--stdin', 'filter'), paths).split(b'\0')
    if {fields[i + 2] for i in range(0, len(fields) - 2, 3)} & configured:
        raise ExternalFilterConfigured()


def protected_snapshot(cwd, *, baseline=None, content_paths=None):
    """Bounded Git evidence; never reads untracked contents. No index refresh.

    Compare working-tree diff against a FIXED baseline across both captures.
    This includes staged and unstaged tracked changes, including already dirty
    contents. Membership transitions are conservatively project-state changes.
    """
    cwd = Path(cwd).resolve()
    env = git_environment()
    base = ('git', '--no-optional-locks', '-c', 'core.fsmonitor=false', '-c', 'core.untrackedCache=false', '-C', str(cwd))
    def read(*args, digest=False, optional=False):
        limit = 64 * 1024 * 1024 if digest else 1024 * 1024
        process = subprocess.Popen((*base, *args), stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL,
                                   env=env)
        timer = threading.Timer(30, process.kill)
        timer.daemon = True
        timer.start()
        output, size, hasher = [], 0, hashlib.sha256()
        try:
            while chunk := process.stdout.read(65536):
                size += len(chunk)
                if size > limit:
                    raise NattaError('Protected snapshot exceeds bounded evidence limit')
                if digest: hasher.update(chunk)
                else: output.append(chunk)
            code = process.wait()
            if code and not (optional and code == 1):
                raise NattaError('Protected Git snapshot unavailable')
            return hasher.hexdigest() if digest else b''.join(output)
        finally:
            timer.cancel()
            if process.poll() is None: process.kill()
            process.wait()
            process.stdout.close()
    top = Path(os.fsdecode(read('rev-parse', '--show-toplevel')).strip()).resolve()
    if top != cwd:
        raise NattaError('Protected project must be a repository root')
    gitdir = Path(os.fsdecode(read('rev-parse', '--absolute-git-dir')).strip()).resolve()
    branch = read('symbolic-ref', '-q', 'HEAD', optional=True).strip()
    # An unborn branch has no commit; unexpected rev-parse failures are refused.
    commit = read('rev-parse', '--verify', '--quiet', 'HEAD', optional=True).strip()
    if not commit and not branch:
        raise NattaError('Invalid protected HEAD')
    if baseline is None:
        if commit:
            baseline = commit.decode('ascii')
        else:
            # Algorithm-aware empty-tree identity, calculated without writing.
            baseline = subprocess.check_output((*base, 'hash-object', '-t', 'tree', '--stdin'),
                                               input=b'', timeout=30, env=env).decode('ascii').strip()
    if not isinstance(baseline, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', baseline):
        raise NattaError('Invalid protected baseline')
    index_path = gitdir / 'index'
    index = None
    if index_path.exists():
        if index_path.is_symlink(): raise NattaError('Protected index cannot be a symlink')
        hasher, size = hashlib.sha256(), 0
        with index_path.open('rb') as stream:
            while chunk := stream.read(65536):
                size += len(chunk)
                if size > 64 * 1024 * 1024: raise NattaError('Protected index exceeds evidence limit')
                hasher.update(chunk)
        index = hasher.hexdigest()
    refuse_applied_filters(cwd)  # Never run an external clean/process filter.
    entries = read('ls-files', '-v', '-z').split(b'\0')
    if any(entry and (entry[:1].islower() or entry[:1] == b'S') for entry in entries):
        raise NattaError('Protected snapshot refuses assume-unchanged/skip-worktree entries')
    staged = read('ls-files', '--stage', '-z', digest=True)
    changes = read('diff', '--binary', '--no-ext-diff', '--no-textconv', '--ignore-submodules=none',
                   '--no-renames', baseline, '--', digest=True)
    untracked = tuple(sorted(set(read('ls-files', '--others', '--exclude-standard', '-z').split(b'\0')) - {b''}))
    contents = None
    if content_paths is not None:
        # Commit changes tracked membership. Hash the same physical paths across
        # captures, including not-ignored untracked inputs. Never follow symlinks.
        paths = set(read('ls-files','--cached','--others','--exclude-standard','-z').split(b'\0')) - {b''}
        paths.update(content_paths)
        if len(paths)>20000:raise NattaError('Commit snapshot path limit exceeded')
        values=[];total=0
        for raw in sorted(paths):
            relative=Path(os.fsdecode(raw))
            if relative.is_absolute() or '..' in relative.parts:raise NattaError('Invalid Git evidence path')
            file=cwd/relative
            if any(parent.is_symlink() for parent in file.parents if parent!=cwd and parent.is_relative_to(cwd)):
                raise NattaError('Commit snapshot parent symlink')
            if file.is_symlink():
                digest=hashlib.sha256(os.fsencode(os.readlink(file))).hexdigest()
            elif file.is_file():
                hasher=hashlib.sha256()
                with file.open('rb') as stream:
                    while chunk:=stream.read(65536):
                        total+=len(chunk)
                        if total>256*1024*1024:raise NattaError('Commit snapshot content limit exceeded')
                        hasher.update(chunk)
                digest=hasher.hexdigest()
            else:digest=None
            # Include permission changes, not just bytes. Directories (submodules)
            # are opaque here; their Git state remains visible in project_state.
            kind=oct(file.lstat().st_mode) if file.exists() or file.is_symlink() else 'missing'
            values.append((raw, None if digest is None else kind+':'+digest))
        contents=tuple(values)
    return ProtectedSnapshot(str(cwd), str(gitdir), baseline, commit, branch, index, changes, untracked, staged, contents)


def bounded_text(value, limit=240):
    """One bounded terminal-safe line, including for provider diagnostics."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", value)[:limit]


class ExecutionDirectory:
    """Current-run ownership and diagnostics only; no run-history framework."""
    def __init__(self, projects, project, operation, level=None, verbose=False, log=False):
        self.projects = projects
        self.verbose, self.log = verbose, log
        self.path = Path(tempfile.mkdtemp(prefix="natta-")).resolve()
        self.identity = (self.path.stat().st_dev, self.path.stat().st_ino)
        self.token = uuid.uuid4().hex
        self.marker = self.path / ".natta-owner"
        self.retained = None
        self.logs = []
        self.bundles = []
        self.run_name = datetime.now().strftime("%Y-%m-%dT%H%M%S") + f"-{project.alias}-{operation}" + (f"-l{level}" if level else "")
        if any(self.path.is_relative_to(p.path) for p in projects):
            self.path.rmdir()  # Newly-created, empty directory only.
            raise NattaError("Temporary execution state must be outside registered repositories")
        try:
            self.marker.write_text(self.token)
        except OSError:
            self.path.rmdir()
            raise

    def cleanup(self):
        # Never accept a user path: identity and a private marker must both match.
        if (self.path.is_symlink() or not self.path.is_dir()
                or (self.path.stat().st_dev, self.path.stat().st_ino) != self.identity
                or self.marker.is_symlink() or not self.marker.is_file()
                or self.marker.read_text() != self.token
                or any(self.path.is_relative_to(p.path) for p in self.projects)):
            raise NattaError("Refusing cleanup: current Natta execution directory ownership changed")
        shutil.rmtree(self.path)

    def retain(self):
        if not self.log:
            return
        root = (Path.home() / "Library/Logs/NattaToolkit").resolve()
        if any(root.is_relative_to(p.path) for p in self.projects):
            raise NattaError("Retained diagnostics must be outside registered repositories")
        root.mkdir(parents=True, exist_ok=True)
        # Atomic creation prevents collisions/overwriting an earlier logged run.
        self.retained = Path(tempfile.mkdtemp(prefix=self.run_name + "-", dir=root))
        for source in self.logs:
            shutil.copy2(source, self.retained / source.name)
        for source in self.bundles:
            if source.is_dir() and not source.is_symlink() and source.resolve().is_relative_to(self.path):
                shutil.copytree(source, self.retained / source.name, symlinks=True)
        print(f"\nDiagnostics retained:\n  {self.retained}", flush=True)

    def run(self, command, cwd, capture=False, timeout=None, *, name=None):
        stem = re.sub(r"[^A-Za-z0-9_-]+", "-", name or f"Discovery-{len(self.logs) + 1}").strip("-")
        log_path = self.path / (stem + ".log")
        self.logs.append(log_path)
        env = child_environment(command)
        with log_path.open("wb") as output:
            if capture:
                # Discovery needs separate stdout for JSON; these queries already
                # used capture_output before the execution UX change.
                process = subprocess.run(list(command), cwd=cwd, capture_output=True,
                                         text=True, timeout=timeout, env=env)
                raw = (process.stdout or "") + (process.stderr or "")
                output.write(raw.encode("utf-8", errors="replace"))
                if self.verbose:
                    print(raw, end="", flush=True)
                return process
            if self.verbose:
                print(f"\nRUN  {name}\n$ {shlex.join(command)}", flush=True)
            # Bounded memory, immediate raw output in verbose mode; merged stderr
            # preserves tool interleaving in the diagnostic log.
            with subprocess.Popen(list(command), cwd=cwd, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, env=env) as process:
                decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                while chunk := process.stdout.read1(65536):
                    output.write(chunk)
                    if self.verbose:
                        print(decoder.decode(chunk), end="", flush=True)
                if self.verbose:
                    print(decoder.decode(b"", final=True), end="", flush=True)
                code = process.wait()
            return subprocess.CompletedProcess(command, code)


def xcode_details(command, cwd, reader=run):
    """Read xcresulttool's structured summary; extraction never determines status."""
    if not command or command[0] != "xcodebuild" or "-resultBundlePath" not in command:
        return (), ()
    bundle = Path(command[command.index("-resultBundlePath") + 1])
    if not bundle.is_dir():
        return (), ()
    try:
        process = reader(("xcrun", "xcresulttool", "get", "test-results", "summary",
                          "--path", str(bundle), "--compact"), cwd, capture=True, timeout=30)
        if process.returncode:
            return (), ()
        summary = json.loads(process.stdout)
        if not isinstance(summary, dict):
            return (), ()
        lines, identifiers = [], []
        for field, label in (("passedTests", "passed"), ("failedTests", "failed"),
                             ("skippedTests", "skipped"), ("expectedFailures", "expected failures")):
            count = summary.get(field)
            if type(count) is int and count >= 0 and (count or field == "passedTests"):
                lines.append(f"{count} test{'s' if count != 1 else ''} {label}")
        failures = summary.get("testFailures", [])
        if isinstance(failures, list):
            for failure in failures:
                if not isinstance(failure, dict):
                    continue
                identifier = failure.get("testIdentifierString")
                if isinstance(identifier, str) and identifier.strip() and identifier not in identifiers:
                    identifiers.append(identifier)
                    if len(identifiers) <= 5:
                        lines.append(bounded_text(identifier))
                        message = failure.get("failureText")
                        if isinstance(message, str) and message.strip():
                            lines.append(bounded_text(message))
        if len(identifiers) > 5:
            lines.append(f"{len(identifiers) - 5} more failing test identifiers in diagnostics")
        return tuple(lines), tuple(identifiers)
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        return (), ()


def build_errors(path):
    """Only explicit compiler/Xcode error markers; never infer a build outcome."""
    lines = []
    try:
        with path.open(errors="replace") as log:
            for line in log:
                if re.search(r"(?:^|: )error:", line):
                    detail = bounded_text(line.strip())
                    if detail not in lines:
                        lines.append(detail)
                    if len(lines) == 3:
                        break
    except OSError:
        return ()  # Diagnostic extraction must not change the process result.
    return tuple(lines)


def execute(checks, cwd, runner=run, baseline=None, *, session=None):
    results = []
    for check in checks:
        if check.status:
            results.append(Result(check, check.status, 0, 0, check.reason))
            continue
        start = time.monotonic()
        try:
            process = (session.run(check.command, cwd, name=check.name) if session
                       else runner(check.command, cwd, capture=True))
            code = process.returncode if process.returncode >= 0 else 128 - process.returncode
            duration = time.monotonic() - start
            lines, identifiers = xcode_details(check.command, cwd)
            if session and "-resultBundlePath" in check.command:
                session.bundles.append(Path(check.command[check.command.index("-resultBundlePath") + 1]))
            if code and session and check.command[0] == "xcodebuild":
                lines += build_errors(session.logs[-1])
            result = Result(check, "passed" if code == 0 else "failed", code, duration,
                            detail_lines=lines, failing_tests=identifiers)
        except OSError as exc:
            result = Result(check, "failed", 127, time.monotonic() - start, str(exc))
        results.append(result)
        if baseline is not None:
            assert_integrity(cwd, baseline, check.name)
        # Preserve independent diagnostics after a failed check; never fix or retry.
    return results


def render(results):
    labels = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP", "unavailable": "UNAVAILABLE"}
    for result in results:
        suffix = f" — {bounded_text(result.detail)}" if result.detail else ""
        if result.status == "failed":
            suffix += f" (exit {result.exit_code})"
        print(f"{labels[result.status]}  {result.check.name}{suffix}")
        for line in result.detail_lines:
            print(f"      {bounded_text(line)}")
        if result.check.command:
            seconds = int(result.duration)
            print(f"      {seconds // 60}m {seconds % 60:02d}s" if seconds >= 60 else f"      {seconds}s")
    code = next((r.exit_code for r in results if r.status == "failed"), 0)
    incomplete = any(r.status == "unavailable" and not r.check.optional for r in results)
    print(f"\nResult: {'failed' if code else 'unavailable' if incomplete else 'passed with omissions' if any(r.status in ('skipped', 'unavailable') for r in results) else 'passed'}")
    if code:
        print("\nFor full diagnostics, rerun with --log or --verbose.")
    return code or int(incomplete)


def assert_integrity(cwd, baseline, label):
    if isinstance(baseline, ProtectedSnapshot):
        after = snapshot(cwd, protected=True, baseline=baseline.baseline)
        if after != baseline:
            raise NattaError(f"Protected repository state changed after {label}; stopped without restoration")
        return
    after = snapshot(cwd)
    if after != baseline:
        changed = sorted(os.fsdecode(p) for p in baseline["files"].keys() | after["files"].keys()
                         if baseline["files"].get(p) != after["files"].get(p)
                         or (p in baseline["files"]) != (p in after["files"]))
        raise NattaError(f"Repository integrity changed after {label}: {changed or 'Git state/index changed'}; stopped without restoration")
