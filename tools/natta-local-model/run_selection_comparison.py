"""Sequential supervised Phase 4C evaluation; no download or capability execution."""
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare
import compare_selection
import evaluate_selection
import local_model as contract
from selection import STACK

ROOT = Path(__file__).resolve().parent


def main():
    evaluate_selection.verify_controls()
    baseline = compare.load_result(ROOT/'evaluation/baseline-qwen3-1.7b.json', 'qwen3-1.7b')
    metadata = contract.inspect_runtime(ROOT)
    if metadata['versions'] != STACK or metadata['python'] != [3,12,15]:
        raise ValueError('Installed stack differs from retained control; stop')
    contract.validate_model(contract.model_path(profile='qwen3-1.7b'), 'qwen3-1.7b')
    env = contract.offline_environment()
    env['PYTORCH_ENABLE_MPS_FALLBACK'] = '0'
    for mode, limit in evaluate_selection.LIMITS.items():
        # Completed result reuse supports continuing after a controlled timeout; no partial baseline is accepted.
        if compare_selection.load(mode, baseline) is not None:
            print(f'{mode}: validated complete retained evaluation; preserving it', flush=True)
            continue
        output = ROOT/f'evaluation/baseline-qwen3-1.7b-{mode}.json'
        print(f'{mode}: starting offline MPS/FP32 evaluation; {limit//60}-minute hard limit', flush=True)
        subprocess.run([str(ROOT/'.venv/bin/python'), '-I', '-B', str(ROOT/'evaluate_selection.py'),
                        '--mode', mode, '--output', str(output)], cwd=ROOT, env=env,
                       check=True, timeout=limit+15)
        compare_selection.main()
    compare_selection.main()


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'Phase 4C stopped: {exc}', file=sys.stderr)
        sys.exit(1)
