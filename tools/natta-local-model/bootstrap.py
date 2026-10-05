"""Explicit installation only. Doctor and inference never invoke this script."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "natta"))
import local_model as contract


def main():
    contract.validate_config(ROOT)
    uv = shutil.which("uv")
    if uv is None:
        raise ValueError("uv is required; install it explicitly before setup")
    if not (ROOT / "uv.lock").is_file():
        raise ValueError("uv.lock missing: first resolve/review dependencies with uv lock --python 3.12 --managed-python --no-python-downloads")
    env = {**os.environ, "UV_PYTHON_DOWNLOADS": "never"}
    # --locked validates declaration consistency; does not silently update lock.
    subprocess.run([uv, "sync", "--locked", "--python", "3.12", "--managed-python",
                    "--no-python-downloads"], cwd=ROOT, env=env, check=True)
    contract.inspect_runtime(ROOT)
    python = ROOT / ".venv/bin/python"
    subprocess.run([str(python), "-I", "-B", str(ROOT / "download.py")],
                   cwd=ROOT, env=env, check=True)
    contract.validate_model(contract.model_path())
    print("Local runtime installed; run decision.py --smoke explicitly to test inference.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Local runtime bootstrap failed: {exc}", file=sys.stderr)
        sys.exit(1)
