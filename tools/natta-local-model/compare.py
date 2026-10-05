"""Read retained evaluation JSON and render comparison; never load a model."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evaluate as evaluation
import local_model as contract

BASELINE_SHA256 = 'RECORD_REVIEWED_PUBLIC_BASELINE_DIGEST_BEFORE_COMPARISON'


def load_result(path, profile):
    raw = path.read_bytes()
    if profile == contract.DEFAULT_PROFILE and hashlib.sha256(raw).hexdigest() != BASELINE_SHA256:
        raise ValueError('Retained 0.6B baseline bytes differ from immutable evidence')
    result = json.loads(raw)
    approved = contract.model_profile(profile)
    runtime = result['runtime']
    if (result['schema_version'] != 1 or result['evaluation_only'] is not True or
            result['total_cases'] != 72 or result['total_decisions'] != 865 or
            len(result['predictions']) != 864 or
            runtime['model_id'] != approved.model_id or runtime['model_revision'] != approved.revision or
            runtime['openjev_commit'] != contract.COMMIT or runtime['backend'] != 'pytorch-mps' or
            runtime['dtype'] != 'float32' or runtime['offline'] is not True):
        raise ValueError('Incomplete or incompatible model comparison result')
    cases, descriptions = evaluation.read_inputs(evaluation.ROOT/'corpus.json', evaluation.ROOT/'descriptions.json')
    experiment = result['experiment']
    if (experiment['corpus'] != cases or experiment['descriptions'] != descriptions or
            experiment['instructions'] != evaluation.INSTRUCTIONS or
            experiment['orders'] != {m: evaluation.orders(m == 'with_no_match')
                                     for m in ('with_no_match', 'without_no_match')} or
            experiment['concentration_thresholds'] != list(evaluation.THRESHOLDS)):
        raise ValueError('Benchmark inputs or configuration differ')
    for filename, digest in result['input_sha256'].items():
        if hashlib.sha256((evaluation.ROOT/filename).read_bytes()).hexdigest() != digest:
            raise ValueError('Benchmark input hash differs')
    expected = {(c['id'], d, m, o) for c in cases for d in descriptions
                for m in ('with_no_match', 'without_no_match') for o in evaluation.orders(m == 'with_no_match')}
    actual = [(r['case_id'], r['description_set'], r['choice_mode'], r['order']) for r in result['predictions']]
    if len(set(actual)) != 864 or set(actual) != expected:
        raise ValueError('Missing, duplicate, or unexpected decisions')
    for row in [*result['predictions'], result['legacy_smoke']['answer']]:
        if row['model']['id'] != approved.model_id or row['model']['revision'] != approved.revision:
            raise ValueError('Prediction model identity differs')
    if evaluation.summarize(result['predictions'], cases) != result['summaries']:
        raise ValueError('Retained summaries differ from unchanged metrics')
    if result['legacy_smoke']['request'] != evaluation.SMOKE:
        raise ValueError('Legacy regression changed')
    return result


def compatible(a, b):
    if a['experiment'] != b['experiment'] or a['input_sha256'] != b['input_sha256']:
        raise ValueError('Experiments are not directly comparable')
    for key in ('openjev_commit', 'backend', 'dtype', 'offline', 'python_version', 'uv_lock_sha256'):
        if a['runtime'][key] != b['runtime'][key]:
            raise ValueError(f'Runtime comparison differs: {key}')


def percent(value):
    return 'n/a' if value is None else f'{value:.1%}'


def empirical_verdict(a, b):
    if b['verdict'] == 'CANDIDATE FOR FURTHER EVALUATION':
        return 'CANDIDATE FOR CALIBRATION / FURTHER SAFETY EVALUATION'
    return 'REJECTED FOR ROUTING'



def report(a, b=None):
    if b is not None:
        compatible(a, b)
    lines = ['# Qwen3 capacity comparison', '',
             'Qwen3-0.6B is rejected as the Natta routing model. Its retained evidence is immutable.', '',
             '1.7B status: ' + (empirical_verdict(a, b) if b else
                              'NOT EVALUATED — sandbox denied the approved Library storage path; MPS is unavailable in this session.'), '',
             'Same OpenJEV, MPS/FP32, schema version 1, 72 cases and 865 decisions. No execution authority.', '',
             '| Description / metric | 0.6B | 1.7B | Delta (percentage points) |',
             '|---|---:|---:|---:|']
    def row(label, x, y=None, is_percent=True):
        fmt = percent if is_percent else str
        delta = f'{100*(y-x):+.1f}' if is_percent and x is not None and y is not None else '—'
        lines.append(f'| {label} | {fmt(x)} | {fmt(y) if b else "pending"} | {delta} |')
    for d in ('concise', 'precise'):
        x = a['summaries'][d]['with_no_match']
        y = b['summaries'][d]['with_no_match'] if b else None
        for label, getter in (
                ('Accuracy', lambda s: s['accuracy']),
                ('Correct / 216', lambda s: s['correct']),
                ('Canonical accuracy', lambda s: s['canonical']['accuracy']),
                ('Order stability', lambda s: s['order_stability']['rate']),
                ('Stable / 72', lambda s: s['order_stability']['stable_cases']),
                ('no_match precision', lambda s: s['no_match']['precision']),
                ('no_match recall', lambda s: s['no_match']['recall']),
                ('no_match true positives / 66', lambda s: s['no_match']['true_positive']),
                ('High-concentration wrong predictions', lambda s: len(s['confidently_wrong'])),
                ('Distinct confidently-wrong cases', lambda s: len({r['case_id'] for r in s['confidently_wrong']}))):
            is_percent = label in ('Accuracy', 'Canonical accuracy', 'Order stability', 'no_match precision', 'no_match recall')
            row(d+' / '+label, getter(x), getter(y) if y else None, is_percent)
        for cap in evaluation.CAPABILITIES:
            row(d+' / '+cap, x['per_capability'][cap]['accuracy'], y['per_capability'][cap]['accuracy'] if y else None)
        for mode in ('with_no_match', 'without_no_match'):
            sx = a['summaries'][d][mode]
            sy = b['summaries'][d][mode] if b else None
            row(d+' / '+mode+' accuracy', sx['accuracy'], sy['accuracy'] if sy else None)
            row(d+' / '+mode+' in-domain accuracy', sx['in_domain_accuracy'], sy['in_domain_accuracy'] if sy else None)
    lines += ['', '| Timing | 0.6B seconds | 1.7B seconds |', '|---|---:|---:|']
    for key, value in a['timing'].items():
        lines.append(f'| {key} | {value:.6f} | {b["timing"][key]:.6f} |' if b else f'| {key} | {value:.6f} | pending |')
    lines += ['', '## Concentration, confusion and regression evidence', '']
    for label, result in [('0.6B', a)] + ([('1.7B', b)] if b else []):
        smoke = result['legacy_smoke']
        lines += [f'### {label}', '', f'Legacy compilation regression: {smoke["answer"]["selected_id"]}; correct={smoke["correct"]}; concentration={smoke["answer"]["concentration"]:.4f}.', '']
        for d in ('concise', 'precise'):
            for mode in ('with_no_match', 'without_no_match'):
                s = result['summaries'][d][mode]
                errors = sorted((c for c in s['confusion'] if c['expected'] != c['selected']), key=lambda c: (-c['count'], c['expected'], c['selected']))
                lines += [f'**{d}, {mode}**', '',
                          'Confusions (all): '+json.dumps(errors, sort_keys=True), '',
                          'Concentration distributions: '+json.dumps(s['concentration'], sort_keys=True), '',
                          'Threshold reporting: '+json.dumps(s['thresholds'], sort_keys=True), '',
                          'Confidently wrong: '+json.dumps(s['confidently_wrong'], sort_keys=True), '',
                          'Per-capability counts: '+json.dumps(s['per_capability'], sort_keys=True), '']
    lines += ['Concentration is uncalibrated. Order instability, high-concentration errors and no_match failures remain barriers to routing.', '',
              'Recommendation: '+('continue with 1.7B to complete the approved experiment; no 4B preflight yet.' if b is None else
                                  'continue offline evaluation only if material improvement justifies it; production routing remains disabled.'), '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    a = load_result(evaluation.ROOT/'baseline-qwen3-0.6b.json', contract.DEFAULT_PROFILE)
    b = load_result(args.candidate, 'qwen3-1.7b') if args.candidate else None
    text = report(a, b)
    if args.output:
        if args.output.resolve() in {(evaluation.ROOT/'BASELINE.md').resolve(), (evaluation.ROOT/'baseline-qwen3-0.6b.json').resolve()}:
            raise ValueError('Cannot overwrite historical baseline evidence')
        with args.output.open('x') as file:
            file.write(text)
    else:
        print(text)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as exc:
        print(f'Comparison failed: {exc}', file=sys.stderr)
        sys.exit(1)
