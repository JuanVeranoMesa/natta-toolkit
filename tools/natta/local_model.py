"""Stdlib-only inspection contract for the optional Phase 4A runtime.

No installation, third-party import, inference, or network operation occurs here.
"""
import json
import os
from pathlib import Path
import subprocess
import tomllib
from urllib.parse import urlsplit
from types import MappingProxyType
from typing import NamedTuple

REPOSITORY = "https://github.com/lookski/openjev"
COMMIT = "67eedd02d8863dfe37fcd391c55ce2a7b6d82e37"
MODEL_ID = "Qwen/Qwen3-0.6B"
REVISION = "c1899de289a04d12100db370d81485cdf75e47ca"
CONFIG = {
    "schema_version": 1, "repository": REPOSITORY, "commit": COMMIT,
    "model_id": MODEL_ID, "model_revision": REVISION,
    "backend": "pytorch-mps", "device": "mps", "dtype": "float32",
    "cloud_fallback": False,
}
MODEL_FILES = (
    "config.json", "tokenizer_config.json", "tokenizer.json",
    "model.safetensors", "LICENSE",
)


DEFAULT_PROFILE = "qwen3-0.6b"


class ModelProfile(NamedTuple):
    model_id: str
    revision: str
    directory: str
    hidden_size: int
    files: tuple[str, ...]


PROFILES = MappingProxyType({
    DEFAULT_PROFILE: ModelProfile(MODEL_ID, REVISION, "Qwen3-0.6B", 1024, MODEL_FILES),
    "qwen3-1.7b": ModelProfile(
        "Qwen/Qwen3-1.7B", "70d244cc86ccca08cf5af4e1e306ecf908b1ad5e",
        "Qwen3-1.7B", 2048,
        ("config.json", "tokenizer_config.json", "tokenizer.json", "LICENSE",
         "model.safetensors.index.json", "model-00001-of-00002.safetensors",
         "model-00002-of-00002.safetensors")),
})


def model_profile(name=DEFAULT_PROFILE):
    if not isinstance(name, str) or name not in PROFILES:
        raise ValueError(f"Unapproved model profile: {name!r}")
    return PROFILES[name]


def runtime_path(workspace=None):
    workspace = Path(workspace) if workspace is not None else Path(__file__).resolve().parents[2]
    return (workspace / "tools/natta-local-model").resolve()


def model_path(home=None, profile=DEFAULT_PROFILE):
    home = Path(home) if home is not None else Path.home()
    approved = model_profile(profile)
    return (home / "Library/Application Support/NattaToolkit/models" / approved.directory / approved.revision).resolve()


def offline_environment():
    return {**os.environ, "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
            "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1", "TRANSFORMERS_OFFLINE": "1",
            "PYTHONDONTWRITEBYTECODE": "1"}


def validate_config(runtime):
    config = tomllib.loads((Path(runtime) / "runtime.toml").read_text())
    if config != CONFIG:
        raise ValueError("Local runtime configuration differs from the approved manifest")


def validate_model(path, profile=DEFAULT_PROFILE):
    """Cheap presence/config/receipt validation. Does not hash/load model weights."""
    approved = model_profile(profile)
    path = Path(path)
    for name in (*approved.files, "natta-model.json"):
        file = path / name
        if not file.is_file() or file.stat().st_size == 0:
            raise ValueError(f"Missing local model artifact: {file}")
    config = json.loads((path / "config.json").read_text())
    if config.get("model_type") != "qwen3" or config.get("architectures") != ["Qwen3ForCausalLM"]:
        raise ValueError("Local model config is not Qwen3ForCausalLM")
    # Fixed properties of the selected approved checkpoint, not just its family name.
    for key, value in {"hidden_size": approved.hidden_size, "num_hidden_layers": 28,
                       "vocab_size": 151936, "num_key_value_heads": 8}.items():
        if config.get(key) != value:
            raise ValueError(f"Local {approved.directory} config mismatch: {key}")
    if "model.safetensors.index.json" in approved.files:
        index = json.loads((path / "model.safetensors.index.json").read_text())
        shards = {name for name in approved.files if name.endswith(".safetensors")}
        weight_map = index.get("weight_map", {})
        if not weight_map or set(weight_map.values()) != shards:
            raise ValueError("Local model shard index differs from approved artifacts")
    receipt = json.loads((path / "natta-model.json").read_text())
    if receipt.get("model_id") != approved.model_id or receipt.get("revision") != approved.revision or receipt.get("source") != "https://huggingface.co":
        raise ValueError("Local model receipt differs from the approved identity")
    records = receipt.get("files", {})
    for name in approved.files:
        record = records.get(name, {})
        if record.get("size") != (path / name).stat().st_size or not isinstance(record.get("sha256"), str) or len(record["sha256"]) != 64:
            raise ValueError(f"Local model receipt incomplete or size mismatch: {name}")
    return receipt


def inspect_runtime(runtime):
    runtime = Path(runtime)
    validate_config(runtime)
    python = runtime / ".venv/bin/python"
    if not python.is_file():
        raise ValueError(f"Missing isolated environment: {python}")
    if not (runtime / "uv.lock").is_file():
        raise ValueError("Missing local runtime uv.lock")
    lock = tomllib.loads((runtime / "uv.lock").read_text())
    packages = [p for p in lock.get("package", []) if p.get("name") == "openjev"]
    if len(packages) != 1:
        raise ValueError("uv.lock must contain exactly one OpenJEV dependency")
    source = urlsplit(packages[0].get("source", {}).get("git", ""))
    if f"{source.scheme}://{source.netloc}{source.path}".removesuffix(".git") != REPOSITORY or source.fragment != COMMIT:
        raise ValueError("uv.lock does not resolve OpenJEV to the approved commit")
    # Isolated stdlib metadata probe: never imports torch, transformers or openjev.
    result = subprocess.run([str(python), "-I", "-B", str(runtime / "probe.py")],
                            capture_output=True, text=True, timeout=15,
                            env=offline_environment())
    if result.returncode:
        raise ValueError((result.stderr or result.stdout).strip() or "Runtime metadata probe failed")
    data = json.loads(result.stdout)
    if data.get("commit") != COMMIT or data.get("repository") != REPOSITORY or data.get("python", [])[:2] != [3, 12]:
        raise ValueError("Installed runtime identity mismatch")
    return data


def doctor_checks(runtime=None, model=None):
    default_inspection = model is None
    runtime = runtime_path() if runtime is None else Path(runtime)
    model = model_path() if model is None else Path(model)
    checks = []
    for label, action in (
        ("OpenJEV runtime available (isolated Python 3.12, pinned source, MPS/FP32 config)", lambda: inspect_runtime(runtime)),
        ("Qwen3-0.6B model available (pinned local snapshot metadata)", lambda: validate_model(model)),
    ):
        try:
            action()
            checks.append((True, label))
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
            detail = " ".join(str(exc).split())[:240]
            checks.append((False, f"{label}: {detail}"))
    # A second approved snapshot is optional; absence adds no general-health noise.
    additional = model_path(profile="qwen3-1.7b")
    if default_inspection and additional.is_dir():
        try:
            validate_model(additional, "qwen3-1.7b")
            checks.append((True, "Qwen3-1.7B comparison snapshot available (pinned local metadata)"))
        except (OSError, ValueError, KeyError) as exc:
            checks.append((False, "Qwen3-1.7B comparison snapshot unavailable: " + " ".join(str(exc).split())[:240]))
    return checks
