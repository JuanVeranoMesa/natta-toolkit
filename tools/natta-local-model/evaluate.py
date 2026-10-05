"""Deterministic Phase 4B evaluation. Model choices are data; no dispatch exists."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import signal
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from decision import LocalDecision, SMOKE, translate

RUNTIME = Path(__file__).resolve().parent
ROOT = RUNTIME / 'evaluation'
CAPABILITIES = ('projects', 'status', 'context', 'diff', 'doctor', 'build', 'test', 'verify', 'no_match')
SEED = 20260404
THRESHOLDS = (0.0, 0.5, 0.8, 0.9, 0.95, 0.99)
INSTRUCTIONS = ('Select the single offered Natta operation that best matches the requested outcome. '
                'Choose only the capability; do not extract project names or arguments. '
                'If no_match is offered, select it for insufficient intent or requests outside the offered operations. '
                'Return a choice as data only; do not execute anything.')


def read_inputs(corpus_path, descriptions_path):
    corpus = json.loads(corpus_path.read_text())
    descriptions = json.loads(descriptions_path.read_text())
    validate_corpus(corpus)
    validate_descriptions(descriptions)
    return corpus['cases'], {key: descriptions[key] for key in ('concise', 'precise')}


def validate_corpus(corpus):
    if not isinstance(corpus, dict) or set(corpus) != {'schema_version', 'cases'} or corpus['schema_version'] != 1:
        raise ValueError('Invalid corpus schema')
    if not isinstance(corpus['cases'], list) or not corpus['cases']:
        raise ValueError('Corpus requires cases')
    ids = set()
    for case in corpus['cases']:
        if not isinstance(case, dict) or set(case) != {'id', 'request', 'expected_choice', 'category', 'notes'}:
            raise ValueError('Invalid case schema')
        if any(not isinstance(value, str) or not value.strip() for value in case.values()):
            raise ValueError('Case fields must be nonempty text')
        if case['expected_choice'] not in CAPABILITIES:
            raise ValueError('Invalid expected capability')
        if case['id'] in ids:
            raise ValueError('Duplicate case ID')
        ids.add(case['id'])


def validate_descriptions(descriptions):
    if not isinstance(descriptions, dict) or set(descriptions) != {'schema_version', 'concise', 'precise'} or descriptions['schema_version'] != 1:
        raise ValueError('Invalid description schema')
    for name in ('concise', 'precise'):
        choices = descriptions[name]
        if not isinstance(choices, dict) or set(choices) != set(CAPABILITIES):
            raise ValueError('Description capabilities differ from evaluation domain')
        if any(not isinstance(value, str) or not value.strip() for value in choices.values()):
            raise ValueError('Descriptions must be nonempty text')


def orders(include_no_match=True):
    canonical = list(CAPABILITIES if include_no_match else CAPABILITIES[:-1])
    shuffled = canonical.copy()
    random.Random(SEED).shuffle(shuffled)
    return {'canonical': canonical, 'reversed': canonical[::-1], f'shuffle-{SEED}': shuffled}


def score(case, answer, choices):
    # Reuse the runtime validator even for supplied/fake predictions.
    translate({'type': 'choice', 'choice': answer['selected_id'],
               'probabilities': answer['probabilities'], 'confidence': answer['concentration']},
              choices, answer['model']['path'], answer['load_seconds'], answer['inference_seconds'])
    return {'case_id': case['id'], 'expected': case['expected_choice'],
            'correct': answer['selected_id'] == case['expected_choice'], **answer}


def distribution(values):
    if not values:
        return {'count': 0, 'min': None, 'median': None, 'mean': None, 'max': None}
    return {'count': len(values), 'min': min(values), 'median': statistics.median(values),
            'mean': statistics.mean(values), 'max': max(values)}


def metrics(rows):
    n = len(rows)
    correct = sum(row['correct'] for row in rows)
    confusion = Counter((row['expected'], row['selected_id']) for row in rows)
    actual_nm = sum(row['expected'] == 'no_match' for row in rows)
    predicted_nm = sum(row['selected_id'] == 'no_match' for row in rows)
    tp = confusion['no_match', 'no_match']
    def ratio(a, b):
        return a / b if b else None
    return {
        'predictions': n, 'correct': correct, 'incorrect': n-correct, 'accuracy': ratio(correct, n),
        'per_capability': {cap: {'total': sum(r['expected'] == cap for r in rows),
            'correct': confusion[cap, cap], 'accuracy': ratio(confusion[cap, cap], sum(r['expected'] == cap for r in rows))}
            for cap in CAPABILITIES},
        'confusion': [{'expected': a, 'selected': b, 'count': count} for (a, b), count in sorted(confusion.items())],
        'no_match': {'precision': ratio(tp, predicted_nm), 'recall': ratio(tp, actual_nm),
                     'true_positive': tp, 'predicted': predicted_nm, 'gold': actual_nm},
        'concentration': {label: distribution([r['concentration'] for r in rows if r['correct'] == value])
                          for label, value in (('correct', True), ('incorrect', False))},
        'thresholds': [{'threshold': t, 'accepted_cases': len(accepted), 'accepted_coverage': ratio(len(accepted), n),
                        'accepted_accuracy': ratio(sum(r['correct'] for r in accepted), len(accepted)),
                        'confidently_wrong': sum(not r['correct'] for r in accepted)}
                       for t in THRESHOLDS for accepted in [[r for r in rows if r['concentration'] >= t]]],
        'confidently_wrong': [{'case_id': r['case_id'], 'expected': r['expected'], 'selected': r['selected_id'],
                              'concentration': r['concentration'], 'order': r.get('order')}
                             for r in rows if not r['correct'] and r['concentration'] >= 0.9],
    }


def stability(rows):
    grouped = {}
    for row in rows:
        grouped.setdefault(row['case_id'], []).append(row)
    changed = {key: {r['order']: r['selected_id'] for r in values}
               for key, values in grouped.items() if len({r['selected_id'] for r in values}) > 1}
    return {'rate': (len(grouped)-len(changed))/len(grouped) if grouped else None,
            'stable_cases': len(grouped)-len(changed), 'total_cases': len(grouped), 'changed_cases': changed}


def summarize(rows, cases):
    summaries = {}
    for name in ('concise', 'precise'):
        summaries[name] = {}
        for mode in ('with_no_match', 'without_no_match'):
            subset = [r for r in rows if r['description_set'] == name and r['choice_mode'] == mode]
            summary = metrics(subset)
            summary['canonical'] = metrics([r for r in subset if r['order'] == 'canonical'])
            summary['accuracy_by_order'] = {order: metrics([r for r in subset if r['order'] == order])['accuracy']
                                           for order in orders(mode == 'with_no_match')}
            summary['order_stability'] = stability(subset)
            summary['in_domain_accuracy'] = metrics([r for r in subset if r['expected'] != 'no_match'])['accuracy']
            summary['category_accuracy'] = {category: metrics([r for r in subset if r['case_id'] in
                {c['id'] for c in cases if c['category'] == category}])['accuracy'] for category in sorted({c['category'] for c in cases})}
            summaries[name][mode] = summary
    return summaries


def verdict(summaries):
    # A conservative exploratory gate; never grants execution authority.
    for name in ('concise', 'precise'):
        s = summaries[name]['with_no_match']
        if (s['accuracy'] < 0.95 or s['order_stability']['rate'] < 0.95 or s['confidently_wrong'] or
                s['category_accuracy'].get('ambiguous', 0) < 0.95 or s['no_match']['recall'] is None or s['no_match']['recall'] < 0.95):
            return 'NOT READY FOR ROUTING'
    return 'CANDIDATE FOR FURTHER EVALUATION'


def evaluate(cases, descriptions, engine, progress=lambda message: None, max_seconds=900):
    started = time.perf_counter()
    rows = []
    load_seconds = 0.0
    total = len(cases)*2*2*3+1
    for name, choices in descriptions.items():
        for include in (True, False):
            for order_name, order in orders(include).items():
                ordered_choices = {key: choices[key] for key in order}
                for case in cases:
                    if time.perf_counter()-started > max_seconds:
                        raise RuntimeError('Evaluation time limit exceeded; no partial success')
                    answer = engine.decide(case['request'], INSTRUCTIONS, ordered_choices)
                    load_seconds += answer['load_seconds']
                    rows.append({**score(case, answer, ordered_choices), 'description_set': name,
                                 'choice_mode': 'with_no_match' if include else 'without_no_match', 'order': order_name})
                    if len(rows) % 36 == 0:
                        progress(f'Evaluated {len(rows)}/{total} decisions ({time.perf_counter()-started:.1f}s)')
    legacy = engine.decide(**SMOKE)
    load_seconds += legacy['load_seconds']
    elapsed = time.perf_counter()-started
    summaries = summarize(rows, cases)
    latencies = [r['inference_seconds'] for r in rows]+[legacy['inference_seconds']]
    import local_model as contract
    approved = contract.model_profile(getattr(engine, 'profile', contract.DEFAULT_PROFILE))
    return {'schema_version': 1, 'evaluation_only': True, 'total_cases': len(cases), 'total_decisions': total,
            'runtime': {'model_id': approved.model_id, 'model_revision': approved.revision, 'openjev_commit': contract.COMMIT,
                        'model_path': str(engine.model_path), 'backend': 'pytorch-mps', 'dtype': 'float32', 'offline': True,
                        'python_version': sys.version,
                        'uv_lock_sha256': hashlib.sha256((RUNTIME/'uv.lock').read_bytes()).hexdigest()},
            'experiment': {'seed': SEED, 'orders': {mode: orders(mode == 'with_no_match') for mode in ('with_no_match', 'without_no_match')},
                           'instructions': INSTRUCTIONS, 'descriptions': descriptions, 'corpus': cases,
                           'concentration_thresholds': THRESHOLDS, 'confidently_wrong_threshold': 0.9,
                           'score_semantics': 'Normalized candidate token scores and distribution concentration; not calibrated correctness probabilities.',
                           'verdict_policy': 'Both description sets: accuracy/stability/ambiguity accuracy/no_match recall >= .95 and no errors with concentration >= .9; candidate verdict is evaluation only.'},
            'timing': {'model_load_seconds': load_seconds, 'total_evaluation_seconds': elapsed,
                       'average_inference_seconds': statistics.mean(latencies), 'median_inference_seconds': statistics.median(latencies)},
            'summaries': summaries, 'predictions': rows,
            'legacy_smoke': {'request': SMOKE, 'expected': 'build', 'correct': legacy['selected_id'] == 'build', 'answer': legacy},
            'verdict': verdict(summaries)}


def percent(value):
    return 'n/a' if value is None else f'{value:.1%}'


def render(result):
    lines = ['Natta Routing Evaluation', f'Model: {result["runtime"]["model_id"]} (local MPS/FP32, offline)',
             f'Cases: {result["total_cases"]}; decisions: {result["total_decisions"]}']
    for name, modes in result['summaries'].items():
        s = modes['with_no_match']
        lines.extend([f'\n{name.capitalize()} descriptions (with no_match; all orders)',
                      f'  Accuracy: {percent(s["accuracy"])} ({s["correct"]}/{s["predictions"]})',
                      f'  Canonical accuracy: {percent(s["canonical"]["accuracy"])}',
                      f'  Order stability: {percent(s["order_stability"]["rate"])}',
                      f'  no_match precision/recall: {percent(s["no_match"]["precision"])}/{percent(s["no_match"]["recall"])}',
                      f'  Without no_match accuracy: {percent(modes["without_no_match"]["accuracy"])}; in-domain: {percent(modes["without_no_match"]["in_domain_accuracy"])}',
                      f'  Errors with concentration >= .9: {len(s["confidently_wrong"])} predictions / {len({r["case_id"] for r in s["confidently_wrong"]})} distinct cases',
                      '  Per capability: '+', '.join(f'{cap} {percent(v["accuracy"])}' for cap,v in s['per_capability'].items())])
        errors = [c for c in s['confusion'] if c['expected'] != c['selected']]
        lines.append('  Frequent confusions: '+', '.join(f'{c["expected"]}->{c["selected"]} {c["count"]}' for c in sorted(errors, key=lambda c: -c['count'])[:5]))
        lines.append('  Wrong examples: '+', '.join(f'{r["case_id"]}->{r["selected"]} ({r["concentration"]:.4f})' for r in s['confidently_wrong'][:5]))
    lines.extend(['\nTiming: '+', '.join(f'{k}={v:.3f}s' for k,v in result['timing'].items()),
                  'Legacy simulator-compilation smoke: '+result['legacy_smoke']['answer']['selected_id']+
                  f' (concentration {result["legacy_smoke"]["answer"]["concentration"]:.4f})',
                  '\nResult: '+result['verdict'],
                  'Concentration is not calibrated correctness or execution authorization. No capability executed.'])
    return '\n'.join(lines)


def persist(result, output):
    if output is not None:
        # Exclusive creation prevents silent replacement of a previous experiment.
        with output.open('x') as file:
            json.dump(result, file, indent=2, allow_nan=False)
            file.write('\n')


def main(argv=None, engine_factory=LocalDecision):
    parser = argparse.ArgumentParser(description=__doc__)
    import local_model as contract
    parser.add_argument('--model', choices=tuple(contract.PROFILES), default=contract.DEFAULT_PROFILE)
    parser.add_argument('--output', type=Path, help='Explicitly retain detailed JSON; path must not exist')
    args = parser.parse_args(argv)
    def timeout(signum, frame):
        raise RuntimeError('15-minute benchmark limit exceeded; stopped')
    previous = signal.signal(signal.SIGALRM, timeout)
    signal.alarm(900)
    try:
        if args.output is not None and args.output.exists():
            raise ValueError('Output already exists; choose a new path')
        cases, descriptions = read_inputs(ROOT/'corpus.json', ROOT/'descriptions.json')
        engine = engine_factory() if args.model == contract.DEFAULT_PROFILE else engine_factory(args.model)
        result = evaluate(cases, descriptions, engine, progress=lambda text: print(text, file=sys.stderr, flush=True))
        result['input_sha256'] = {filename: hashlib.sha256((ROOT/filename).read_bytes()).hexdigest()
                                  for filename in ('corpus.json', 'descriptions.json')}
        persist(result, args.output)
        print(render(result))
        return 0
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        print(f'Evaluation failed: {exc}', file=sys.stderr)
        return 1
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


if __name__ == '__main__':
    sys.exit(main())
