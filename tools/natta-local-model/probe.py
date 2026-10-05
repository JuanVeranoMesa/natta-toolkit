"""Read installed metadata without importing any third-party runtime modules."""
import importlib.metadata
import json
from pathlib import Path
import sys

REPOSITORY = "https://github.com/lookski/openjev"
COMMIT = "67eedd02d8863dfe37fcd391c55ce2a7b6d82e37"


def inspect():
    if sys.version_info[:2] != (3, 12):
        raise ValueError("Runtime requires isolated Python 3.12")
    expected_environment = Path(__file__).resolve().parent / ".venv"
    if Path(sys.prefix).resolve() != expected_environment.resolve() or sys.prefix == sys.base_prefix:
        raise ValueError("Runtime must run inside its own .venv")
    distribution = importlib.metadata.distribution("openjev")
    source = json.loads(distribution.read_text("direct_url.json") or "{}")
    if source.get("url", "").removesuffix(".git") != REPOSITORY or source.get("vcs_info", {}).get("commit_id") != COMMIT:
        raise ValueError("Installed OpenJEV is not the approved Git source commit")
    for module in ("openjev/core.py", "openjev/types.py", "openjev/__init__.py"):
        if not Path(distribution.locate_file(module)).is_file():
            raise ValueError(f"Missing installed module: {module}")
    versions = {name: importlib.metadata.version(name) for name in
                ("openjev", "torch", "transformers", "huggingface-hub")}
    return {"python": list(sys.version_info[:3]), "repository": REPOSITORY,
            "commit": COMMIT, "versions": versions}


if __name__ == "__main__":
    try:
        print(json.dumps(inspect()))
    except (ValueError, OSError, importlib.metadata.PackageNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
