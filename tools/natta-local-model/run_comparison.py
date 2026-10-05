"""Explicit 1.7B smoke then unchanged benchmark, in sequential isolated processes."""
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare
import local_model as contract

ROOT = Path(__file__).resolve().parent
PROFILE = 'qwen3-1.7b'


def main():
    baseline = compare.load_result(ROOT/'evaluation/baseline-qwen3-0.6b.json', contract.DEFAULT_PROFILE)
    metadata = contract.inspect_runtime(ROOT)
    expected = {'openjev': '0.1.0', 'torch': '2.14.1', 'transformers': '5.18.0', 'huggingface-hub': '1.33.0'}
    if metadata['versions'] != expected or metadata['python'] != [3, 12, 15]:
        raise ValueError('Installed stack differs from approved comparison environment; stop')
    import hashlib
    if hashlib.sha256((ROOT/'uv.lock').read_bytes()).hexdigest() != baseline['runtime']['uv_lock_sha256']:
        raise ValueError('Dependency lock differs from retained baseline; stop')
    paths = [ROOT/'evaluation'/name for name in
             ('smoke-qwen3-1.7b.json', 'baseline-qwen3-1.7b.json', 'COMPARISON-qwen3-1.7b.md')]
    if any(path.exists() for path in paths):
        raise ValueError('Comparison output already exists; preserve it and review before another run')
    contract.validate_model(contract.model_path(profile=PROFILE), PROFILE)
    python = str(ROOT/'.venv/bin/python')
    env = contract.offline_environment()
    env["PYTORCH_ENABLE_MPS_FALLBACK"] = "0"
    print('One explicit offline MPS/FP32 smoke; no capability execution.', flush=True)
    smoke = subprocess.run([python, '-I', '-B', str(ROOT/'decision.py'), '--model', PROFILE, '--smoke'],
                           cwd=ROOT, env=env, capture_output=True, text=True, timeout=900)
    if smoke.returncode:
        raise RuntimeError('Smoke failed; benchmark not started: '+smoke.stdout.strip()+' '+smoke.stderr.strip())
    answer = json.loads(smoke.stdout)
    approved = contract.model_profile(PROFILE)
    if (answer['status'] != 'passed' or answer['device'] != 'mps' or answer['dtype'] != 'float32' or
            answer['model']['id'] != approved.model_id or answer['model']['revision'] != approved.revision or
            answer['model']['path'] != str(contract.model_path(profile=PROFILE))):
        raise ValueError('Smoke did not confirm approved local MPS/FP32 identity; benchmark not started')
    with paths[0].open('x') as file:
        json.dump(answer, file, indent=2, allow_nan=False)
        file.write('\n')
    print(json.dumps(answer), flush=True)
    # Smoke process has exited: its model is released before benchmark loading.
    subprocess.run([python, '-I', '-B', str(ROOT/'evaluate.py'), '--model', PROFILE, '--output', str(paths[1])],
                   cwd=ROOT, env=env, check=True)
    candidate = compare.load_result(paths[1], PROFILE)
    text = compare.report(baseline, candidate)
    with paths[2].open('x') as file:
        file.write(text)
    print(f'Comparison retained: {paths[2]}')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'Controlled comparison stopped: {exc}', file=sys.stderr)
        sys.exit(1)
