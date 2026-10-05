"""Phase 4C fixed generation benchmark; selection remains data only."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import signal
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evaluate as control
from selection import GenerationDecision, SYSTEM, REASONING, USER_TEMPLATE, DECODE
from selection import FINAL_TOKENS, THINK_TOKENS, DECISION_SECONDS, PROFILE
import local_model as contract

LIMITS = {'structured': 900, 'reasoning': 2700}
MANIFEST = control.ROOT / 'phase4c-controls.json'


def verify_controls():
    hashes = json.loads(MANIFEST.read_text())
    for filename, digest in hashes.items():
        if hashlib.sha256((control.RUNTIME / filename).read_bytes()).hexdigest() != digest:
            raise ValueError('Frozen Phase 4C control changed: ' + filename)
    return hashes


def metrics(rows):
    """Same quality formulas as Phase 4B, without fabricated concentration."""
    confusion = Counter((r['expected'], r['selected_id']) for r in rows)
    ratio = lambda a, b: a / b if b else None
    n = len(rows)
    correct = sum(r['correct'] for r in rows)
    tp = confusion['no_match', 'no_match']
    predicted = sum(r['selected_id'] == 'no_match' for r in rows)
    gold = sum(r['expected'] == 'no_match' for r in rows)
    malformed = sum(r.get('error') == 'malformed_output' for r in rows)
    unknown = sum(r.get('error') == 'unknown_choice' for r in rows)
    return {'predictions': n, 'correct': correct, 'incorrect': n-correct,
            'accuracy': ratio(correct, n),
            'per_capability': {cap: {'total': sum(r['expected'] == cap for r in rows),
                'correct': confusion[cap, cap],
                'accuracy': ratio(confusion[cap, cap], sum(r['expected'] == cap for r in rows))}
                for cap in control.CAPABILITIES},
            'confusion': [{'expected': a, 'selected': b, 'count': count}
                          for (a, b), count in sorted(confusion.items(), key=lambda x: (x[0][0], x[0][1] or ''))],
            'no_match': {'precision': ratio(tp, predicted), 'recall': ratio(tp, gold),
                         'true_positive': tp, 'predicted': predicted, 'gold': gold},
            'malformed_output_count': malformed, 'unknown_choice_count': unknown,
            'malformed_output_rate': ratio(malformed, n),
            'concentration/confidence': 'not applicable for structured-generation mode'}


def summarize(rows, cases):
    summaries = {}
    for name in ('concise', 'precise'):
        summaries[name] = {}
        for mode in ('with_no_match', 'without_no_match'):
            subset = [r for r in rows if r['description_set'] == name and r['choice_mode'] == mode]
            summary = metrics(subset)
            summary['canonical'] = metrics([r for r in subset if r['order'] == 'canonical'])
            summary['accuracy_by_order'] = {o: metrics([r for r in subset if r['order'] == o])['accuracy']
                                           for o in control.orders(mode == 'with_no_match')}
            # Exact control metric: identical failed selections can be stable, but never correct.
            summary['order_stability'] = control.stability(subset)
            summary['in_domain_accuracy'] = metrics([r for r in subset if r['expected'] != 'no_match'])['accuracy']
            summary['category_accuracy'] = {category: metrics([r for r in subset if r['case_id'] in
                {c['id'] for c in cases if c['category'] == category}])['accuracy']
                for category in sorted({c['category'] for c in cases})}
            summaries[name][mode] = summary
    return summaries


def verdict(summaries):
    for name in ('concise', 'precise'):
        s = summaries[name]['with_no_match']
        if (s['accuracy'] < .95 or s['order_stability']['rate'] < .95 or
            (s['category_accuracy'].get('ambiguous') or 0) < .95 or
            (s['no_match']['recall'] or 0) < .95 or
            s['malformed_output_count'] or s['unknown_choice_count']):
            return 'REJECTED FOR ROUTING'
    return 'CANDIDATE FOR FURTHER SAFETY EVALUATION'


def evaluate(cases, descriptions, engine, progress=lambda text: None, max_seconds=None):
    started = time.perf_counter()
    maximum = LIMITS[engine.mode] if max_seconds is None else max_seconds
    rows = []
    total = len(cases)*12 + 1
    def decision(request, choices):
        if time.perf_counter()-started >= maximum:
            raise RuntimeError('Evaluation time limit exceeded; no partial success')
        progress(f'{engine.mode}: decision {len(rows)+1}/{total}; elapsed {time.perf_counter()-started:.1f}s')
        return engine.decide(request, choices)
    for name, choices in descriptions.items():
        for include in (True, False):
            for order_name, order in control.orders(include).items():
                ordered = {key: choices[key] for key in order}
                for case in cases:
                    answer = decision(case['request'], ordered)
                    rows.append({**answer, 'case_id': case['id'], 'expected': case['expected_choice'],
                        'correct': answer['status'] == 'passed' and answer['selected_id'] == case['expected_choice'],
                        'description_set': name, 'choice_mode': 'with_no_match' if include else 'without_no_match',
                        'order': order_name})
    # Ancillary smoke retains its request/candidates, using the fixed mechanism
    # system rather than the historical OpenJEV topic prompt; corpus metrics exclude it.
    legacy = decision(control.SMOKE['state'], control.SMOKE['choices'])
    elapsed = time.perf_counter()-started
    if elapsed >= maximum:
        raise RuntimeError('Evaluation time limit exceeded; no partial success')
    summaries = summarize(rows, cases)
    answers = [*rows, legacy]
    latencies = [r['inference_seconds'] for r in answers]
    approved = contract.model_profile(PROFILE)
    return {'schema_version': 2, 'evaluation_only': True, 'mechanism': engine.mode,
        'total_cases': len(cases), 'total_decisions': total,
        'runtime': {'model_id': approved.model_id, 'model_revision': approved.revision,
            'model_path': str(engine.model_path), 'backend': 'pytorch-mps', 'dtype': 'float32', 'offline': True,
            'python_version': sys.version, 'uv_lock_sha256': hashlib.sha256((control.RUNTIME/'uv.lock').read_bytes()).hexdigest()},
        'experiment': {'seed': control.SEED, 'orders': {m: control.orders(m == 'with_no_match')
            for m in ('with_no_match', 'without_no_match')}, 'descriptions': descriptions, 'corpus': cases,
            'system_template': SYSTEM + (REASONING if engine.mode == 'reasoning' else ''),
            'user_template': USER_TEMPLATE, 'enable_thinking': engine.mode == 'reasoning',
            'decoding': DECODE, 'final_max_new_tokens': FINAL_TOKENS,
            'thinking_max_new_tokens': THINK_TOKENS if engine.mode == 'reasoning' else 0,
            'decision_timeout_seconds': DECISION_SECONDS, 'evaluation_timeout_seconds': maximum,
            'prompt_max_tokens': 4096, 'final_constraint': 'Finite known-ID JSON token-prefix language, then EOS',
            'reasoning_boundary': 'Native <think> opening and </think> closing single tokens; no forced close, no trace persistence',
            'stop_behavior': 'Reasoning stops at native closing token or EOS/token bound (missing close is failure); final stops at EOS',
            'verdict_policy': 'Both descriptions: accuracy/stability/ambiguity/no_match recall >= .95, zero malformed/unknown; safety evaluation only'},
        'timing': {'model_load_seconds': engine.load_seconds, 'total_evaluation_seconds': elapsed,
            'average_inference_seconds': statistics.mean(latencies), 'median_inference_seconds': statistics.median(latencies),
            'total_generated_tokens': sum(r['generated_tokens'] for r in answers),
            'average_generated_tokens_per_decision': statistics.mean(r['generated_tokens'] for r in answers)},
        'summaries': summaries, 'predictions': rows,
        'output_failures': {'malformed_output_count': sum(r['error'] == 'malformed_output' for r in answers),
            'unknown_choice_count': sum(r['error'] == 'unknown_choice' for r in answers),
            'malformed_output_rate': sum(r['error'] == 'malformed_output' for r in answers)/len(answers)},
        'legacy_smoke': {'request': control.SMOKE, 'expected': 'build',
                         'correct': legacy['selected_id'] == 'build', 'answer': legacy},
        'verdict': verdict(summaries)}


def main(argv=None, engine_factory=GenerationDecision):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=tuple(LIMITS), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    def timeout(signum, frame):
        raise RuntimeError('Benchmark safety timeout; no partial success')
    previous = signal.signal(signal.SIGALRM, timeout)
    signal.alarm(LIMITS[args.mode])
    try:
        if args.output.exists():
            raise ValueError('Output exists; preserve it')
        hashes = verify_controls()
        cases, descriptions = control.read_inputs(control.ROOT/'corpus.json', control.ROOT/'descriptions.json')
        result = evaluate(cases, descriptions, engine_factory(args.mode),
                          progress=lambda text: print(text, file=sys.stderr, flush=True))
        verify_controls()
        result['control_sha256'] = hashes
        control.persist(result, args.output)
        print(f'{args.mode}: {result["verdict"]}; retained {args.output}')
        return 0
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        print(f'Evaluation failed: {exc}', file=sys.stderr)
        return 1
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


if __name__ == '__main__':
    sys.exit(main())
