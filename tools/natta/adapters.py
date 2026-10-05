"""Small project-type adapters. Registry selects policy; adapters own mechanics."""
import json
import re
from pathlib import Path
import shutil

from execution import Check, NattaError, query, run


def parse_execution(kind, value, root):
    if not isinstance(value, dict):
        raise NattaError("execution must be a table")
    if kind != "ios-xcode":
        if value:
            raise NattaError(f"{kind}: execution configuration is not supported")
        return value
    allowed = {"project", "workspace", "scheme", "configuration", "unit_targets", "ui_targets", "test_targets", "ui_tests"}
    if value.keys() - allowed:
        raise NattaError("Unknown Xcode execution fields")
    if not value:
        return value  # Phase 1 registrations may remain inspection-only.
    if len(value.keys() & {"project", "workspace"}) != 1:
        raise NattaError("execution requires exactly one project or workspace")
    def valid_name(item):
        return isinstance(item, str) and bool(item.strip()) and not item.startswith("-") and not any(ord(c) < 32 for c in item)
    for key in ("scheme", "configuration"):
        if not valid_name(value.get(key)):
            raise NattaError(f"execution.{key} must be a nonempty name")
    for key in ("project", "workspace"):
        if key in value:
            raw = value[key]
            if not valid_name(raw) or Path(raw).is_absolute():
                raise NattaError(f"execution.{key} must be a relative path")
            path = (root / raw).resolve()
            suffix = ".xcodeproj" if key == "project" else ".xcworkspace"
            if not path.is_relative_to(root) or path.suffix != suffix:
                raise NattaError(f"Invalid execution.{key} path")
    for key in ("unit_targets", "ui_targets", "test_targets", "ui_tests"):
        items = value.get(key, [])
        if not isinstance(items, list) or any(not valid_name(x) for x in items) or len(set(items)) != len(items):
            raise NattaError(f"execution.{key} must be an array of unique names")
    if not value.get("unit_targets") or not value.get("test_targets"):
        raise NattaError("execution requires unit_targets and test_targets")
    targets = value["unit_targets"] + value.get("ui_targets", [])
    if len(set(targets)) != len(targets):
        raise NattaError("unit_targets and ui_targets must be disjoint")
    for key in ("test_targets", "ui_tests"):
        expected = targets if key == "test_targets" else value.get("ui_targets", [])
        if any(item.split('/')[0] not in expected for item in value.get(key, [])):
            raise NattaError(f"execution.{key} references an unregistered target")
    return value


class GenericGit:
    def __init__(self, project, runner=run):
        self.project, self.runner = project, runner

    def diagnose(self):
        return [(True, "generic-git adapter (no build/test workflow)")]

    def diff_checks(self):
        base = ("git", "--no-optional-locks", "-C", str(self.project.path), "diff", "--no-ext-diff", "--no-textconv")
        return [Check("git diff --check (unstaged)", (*base, "--check")),
                Check("git diff --check (staged)", (*base, "--cached", "--check"))]

    def plan(self, operation, level, state):
        checks = self.diff_checks() if operation == "verify" else []
        if operation != "verify" or level > 1:
            checks.append(Check("Build/test workflows", status="unavailable", reason="No generic build/test workflow configured"))
        return checks


class IOSXcode(GenericGit):
    def metadata(self):
        config = self.project.execution
        if not config:
            raise NattaError("Xcode execution not configured")
        key = "workspace" if "workspace" in config else "project"
        path = self.project.path / config[key]
        if not path.is_dir():
            raise NattaError(f"Xcode {key} missing: {path}")
        self.base = ("xcodebuild", f"-{key}", str(path))
        try:
            metadata = json.loads(query((*self.base, "-list", "-json", "-disableAutomaticPackageResolution"), self.project.path, self.runner))[key]
            required = ("schemes", "configurations", "targets") if key == "project" else ("schemes",)
            if any(not isinstance(metadata[field], list) or any(not isinstance(item, str) for item in metadata[field]) for field in required):
                raise NattaError("Malformed Xcode metadata name lists")
            if config["scheme"] not in metadata["schemes"]:
                raise NattaError(f"Scheme missing: {config['scheme']}")
            if key == "project" and config["configuration"] not in metadata["configurations"]:
                raise NattaError(f"Configuration missing: {config['configuration']}")
            # Workspace list output does not enumerate targets; Xcode validates its test selections.
            if key == "project" and any(t not in metadata["targets"] for t in config["unit_targets"] + config.get("ui_targets", [])):
                raise NattaError("Configured test target missing from Xcode metadata")
        except (ValueError, KeyError, TypeError) as exc:
            raise NattaError(f"Malformed Xcode metadata: {exc}") from exc
        self.metadata_names = metadata
        return config

    def diagnose(self):
        checks = [(shutil.which("xcodebuild") is not None, "xcodebuild available")]
        try:
            self.metadata()
            checks.append((True, "Xcode container and scheme" if "workspace" in self.project.execution else "Xcode container, scheme, configuration and targets"))
        except NattaError as exc:
            checks.append((False, str(exc)))
        return checks

    def destination(self):
        # Use Xcode's compatible scheme destinations, not a stale configured UDID.
        output = query((*self.base, "-scheme", self.project.execution["scheme"], "-showdestinations"), self.project.path, self.runner)
        section = re.split(r"Ineligible destinations|Unavailable destinations|Destinations incompatible", output)[0]
        candidates = []
        for record in re.findall(r"\{([^{}]+)\}", section):
            if re.search(r"\berror\s*:", record):
                continue
            fields = dict(re.findall(r"(platform|id|OS|name)\s*:\s*([^,}]+)", record))
            fields = {k: v.strip() for k, v in fields.items()}
            if fields.get("platform") == "iOS Simulator" and re.fullmatch(r"[0-9A-Fa-f-]{36}", fields.get("id", "")):
                candidates.append(fields)
        if not candidates:
            raise NattaError("No compatible available iOS Simulator destination reported by Xcode")
        def order(item):
            version = tuple(int(n) for n in re.findall(r"\d+", item.get("OS", "0")))
            version = (version + (0, 0, 0))[:3]
            return (tuple(-n for n in version), not item.get("name", "").startswith("iPhone"), item.get("name", ""), item["id"])
        return "platform=iOS Simulator,id=" + sorted(candidates, key=order)[0]["id"]

    def plan(self, operation, level, state):
        config = self.metadata()
        common = (*self.base, "-scheme", config["scheme"], "-configuration", config["configuration"],
                  "-derivedDataPath", str(state / "DerivedData"), "-clonedSourcePackagesDirPath", str(state / "Packages"),
                  "-disableAutomaticPackageResolution", "CODE_SIGNING_ALLOWED=NO")
        build = Check("Simulator build", (*common, "-destination", "generic/platform=iOS Simulator", "build"))
        need_tests = operation == "test" or (operation == "verify" and level >= 2)
        destination, error = None, None
        if need_tests:
            try:
                destination = self.destination()
            except NattaError as exc:
                error = str(exc)
        def tests(name, selections):
            if not selections:
                return Check(name, status="skipped", reason="No relevant test selection registered")
            if error:
                return Check(name, status="unavailable", reason=error)
            return Check(name, (*common, "-destination", destination, "-parallel-testing-enabled", "NO",
                                "-resultBundlePath", str(state / (name.replace(' ', '-') + '.xcresult')),
                                *(f"-only-testing:{item}" for item in selections), "test"))
        if operation == "build":
            return [build]
        if operation == "test":
            return [tests("Automated tests", config["test_targets"])]
        checks = []
        if level >= 2:
            checks.append(tests("Unit tests", config["unit_targets"]))
        checks.append(build)
        if level >= 2:
            checks.append(tests("UI smoke tests", config.get("ui_tests", [])))
        if level == 3:
            paths = query(("git", "--no-optional-locks", "ls-files", "--cached", "--others", "--exclude-standard", "-z"), self.project.path, self.runner).split('\0')
            files = sorted({str((self.project.path / p).resolve()) for p in paths if p and Path(p).suffix in (".pbxproj", ".plist", ".entitlements", ".xcprivacy") and (self.project.path / p).is_file()})
            if any(not Path(p).is_relative_to(self.project.path) for p in files):
                raise NattaError("Metadata path escapes repository")
            checks.append(Check("Project/plist syntax", ("plutil", "-lint", *files)) if files else Check("Project/plist syntax", status="unavailable", reason="No metadata files found"))
            checks.append(Check("Physical-device validation", status="unavailable", reason="No signed device workflow registered; no installation or build-number mutation", optional=True))
            checks.append(Check("Manual platform/release checks", status="skipped", reason="Geofence hardware, distribution and hands-on upgrade checks are outside automated validation; migration tests run in unit suite where present"))
        return checks + self.diff_checks()


ADAPTERS = {"generic-git": GenericGit, "ios-xcode": IOSXcode}


def resolve(project, runner=run):
    try:
        adapter = ADAPTERS[project.type]
    except KeyError as exc:
        raise NattaError(f"Unsupported adapter: {project.type}") from exc
    return adapter(project, runner)

