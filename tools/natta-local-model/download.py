"""Approved setup only: official pinned snapshot, local-dir storage, no duplicate weights."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "natta"))
import local_model as contract


def hashes(path):
    size = path.stat().st_size
    sha256 = hashlib.sha256()
    blob = hashlib.sha1(f"blob {size}\0".encode())
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            sha256.update(chunk)
            blob.update(chunk)
    return {"size": size, "sha256": sha256.hexdigest(), "git_blob": blob.hexdigest()}


def verify_local(path, profile=contract.DEFAULT_PROFILE):
    receipt = contract.validate_model(path, profile)
    for name, expected in receipt["files"].items():
        if Path(name).name != name:
            raise ValueError("Invalid artifact receipt filename")
        if hashes(path / name) != expected:
            raise ValueError(f"Local artifact hash mismatch: {name}")
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=tuple(contract.PROFILES), default=contract.DEFAULT_PROFILE)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    approved = contract.model_profile(args.model)
    path = contract.model_path(profile=args.model)
    if args.verify:
        print(json.dumps(verify_local(path, args.model)))
        return
    # Override ambient mirror/offline/token settings only in this setup process.
    os.environ.update({"HF_ENDPOINT": "https://huggingface.co", "HF_HUB_OFFLINE": "0",
                       "HF_HUB_DISABLE_TELEMETRY": "1", "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
                       "HF_XET_CHUNK_CACHE_SIZE_BYTES": "0"})
    if (path / "natta-model.json").is_file():
        verify_local(path, args.model)
        print(f"Pinned local snapshot verified: {path}")
        return
    # Fail on storage permissions before contacting the Hub.
    path.mkdir(parents=True, exist_ok=True)
    from huggingface_hub import HfApi, snapshot_download
    info = HfApi(endpoint="https://huggingface.co", token=False).model_info(
        approved.model_id, revision=approved.revision, files_metadata=True)
    if info.sha != approved.revision:
        raise ValueError("Hugging Face resolved a different model revision")
    snapshot_download(approved.model_id, revision=approved.revision,
                      endpoint="https://huggingface.co", token=False, local_dir=path,
                      allow_patterns=["*.json", "*.txt", "*.safetensors", "*.jinja", "LICENSE"])
    files = {}
    for artifact in info.siblings:
        file = path / artifact.rfilename
        if not file.is_file() or file.parent != path:
            continue
        record = hashes(file)
        lfs = artifact.lfs
        if lfs:
            expected = lfs.sha256 if hasattr(lfs, "sha256") else lfs["sha256"]
            if record["sha256"] != expected:
                raise ValueError(f"Upstream LFS checksum mismatch: {file.name}")
        elif record["git_blob"] != artifact.blob_id:
            raise ValueError(f"Upstream Git blob checksum mismatch: {file.name}")
        files[file.name] = record
    if not set(approved.files) <= set(files):
        raise ValueError("Pinned snapshot lacks required verified artifacts")
    receipt = {"model_id": approved.model_id, "revision": approved.revision,
               "source": "https://huggingface.co", "files": files}
    (path / "natta-model.json").write_text(json.dumps(receipt, indent=2) + "\n")
    contract.validate_model(path, args.model)
    print(f"Pinned local snapshot downloaded and verified: {path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Model setup failed: {exc}", file=sys.stderr)
        sys.exit(1)
