"""Phase 4D evaluation only: host pilot/reference/stability/full run, no capability dispatch."""
import argparse
from collections import Counter
import json
import hashlib
import math
from pathlib import Path
import statistics
import subprocess
import sys
import time

RUNTIME = Path(__file__).resolve().parent
sys.path.insert(0, str(RUNTIME.parent / 'natta'))
sys.path.insert(0, str(RUNTIME))
import router_codex as router
import evaluate as control
import evaluate_selection as scoring

PILOT_IDS = ('clear_positive-03', 'clear_positive-05', 'clear_positive-11',
             'ambiguous-01', 'out_of_scope-03', 'adversarial-09')
FULL_COUNT = 72 * 2 * 2 * 3
REFERENCE_MODEL = 'gpt-6-luna'
STABILITY_SOURCE = control.ROOT / 'reference-codex-luna-canonical.json'
STABILITY_ALLOCATION = {'clear_positive': 4, 'natural_variation': 4, 'project_variation': 4,
                        'ambiguous': 4, 'out_of_scope': 3, 'adversarial': 3, 'regression': 2}
STABILITY_THRESHOLDS = {'maximum_failures': 0, 'minimum_fully_stable': 22,
    'preferred_fully_stable': 23, 'minimum_new_order_accuracy': .90,
    'position_bias_two_sided_p': .05, 'dangerous_order_induced_routes': 0,
    'regressions_build_all_orders': True}
SUBSET_METHOD = 'Within each category rank SHA-256 of UTF-8 seed:category:case_id ascending, then case_id; take allocation; emit category allocation order.'

ERRORS = ('malformed_output', 'unknown_choice', 'provider_failure', 'provider_timeout', 'boundary_violation')


def inputs():
    hashes = scoring.verify_controls()
    cases, descriptions = control.read_inputs(control.ROOT / 'corpus.json', control.ROOT / 'descriptions.json')
    if len(cases) != 72 or descriptions != {k: router.definitions()[k] for k in ('concise', 'precise')}:
        raise ValueError('Frozen registry/corpus mismatch')
    return cases, descriptions, hashes


def decisions(cases, mode):
    if mode == 'stability':
        return [(case, 'concise', True, name, control.orders()[name])
                for name in ('reversed', f'shuffle-{control.SEED}') for case in stability_subset(cases)]
    if mode == 'reference':
        return [(case, 'concise', True, 'canonical', control.orders()['canonical']) for case in cases]
    if mode == 'pilot':
        selected = {c['id']: c for c in cases}
        orders = list(control.orders().items())
        return [(selected[key], 'concise' if index < 3 else 'precise', True,
                 orders[index % 3][0], orders[index % 3][1]) for index, key in enumerate(PILOT_IDS)]
    if mode != 'full':
        raise ValueError('Unknown evaluation mode')
    return [(case, name, include, order_name, order)
            for name in ('concise', 'precise') for include in (True, False)
            for order_name, order in control.orders(include).items() for case in cases]


def metadata(model=None):
    version = router.probe_cli(router.workspace_path())
    return {'cli_version': version, 'model': model,
            'model_selection': 'explicit CLI override' if model else
                'implicit installed Codex default; user config ignored, identity not inferred',
            'execution_mode': 'fresh non-interactive codex exec; no shared daemon; ephemeral',
            'structured_output': '--output-schema plus --json agent_message event; strict whole JSON validation',
            'sandbox_mode': 'read-only', 'approval_mode': 'never',
            'ignored_user_config': True, 'disabled_features': list(router.DISABLED),
            'cwd': str(router.workspace_path()), 'argv': router.invocation(router.workspace_path(), model)}


def evaluate(mode, engine, provider_metadata, progress=print):
    if mode == 'reference' and provider_metadata.get('model') != REFERENCE_MODEL:
        raise ValueError('Canonical reference pass requires explicit gpt-6-luna')
    cases, descriptions, hashes = inputs()
    plan = decisions(cases, mode)
    started = time.perf_counter()
    rows = []
    for index, (case, name, include, order_name, order) in enumerate(plan, 1):
        progress(f'{mode}: {index}/{len(plan)} {name}/{order_name}/{case["id"]}')
        answer = engine.decide(case['request'], name, order)
        rows.append({**answer, 'case_id': case['id'], 'expected': case['expected_choice'],
                     'correct': answer['status'] == 'passed' and answer['selected_id'] == case['expected_choice'],
                     'description_set': name, 'choice_mode': 'with_no_match' if include else 'without_no_match',
                     'order': order_name})
        # Stop at an isolation breach instead of continuing provider calls.
        if (answer['error'] == 'boundary_violation' or
                (mode == 'reference' and answer['error'] in ('provider_failure', 'provider_timeout'))):
            break
    if inputs()[2] != hashes:
        raise ValueError('Frozen inputs changed during evaluation')
    if router.source_hashes() != provider_metadata['source_sha256']:
        raise ValueError('Authoritative/exporter sources changed during evaluation')
    latencies = [r['inference_seconds'] for r in rows]
    counts = Counter(r['error'] for r in rows if r['error'])
    result = {'schema_version': 3, 'evaluation_only': True, 'mechanism': 'external-codex',
              'mode': mode, 'complete': len(rows) == len(plan), 'total_cases': len({r['case_id'] for r in rows}),
              'total_decisions': len(rows), 'planned_decisions': len(plan), 'runtime': provider_metadata,
              'control_sha256': hashes, 'experiment': {'seed': control.SEED,
                  'orders': {m: control.orders(m == 'with_no_match') for m in ('with_no_match', 'without_no_match')},
                  'descriptions': descriptions, 'corpus': cases, 'prompt_template': router.PROMPT,
                  'routing_policy': router.POLICY, 'pilot_ids': list(PILOT_IDS) if mode == 'pilot' else None,
                  'batching': False, 'ancillary_legacy_smoke': 'excluded: outside required candidate universe',
                  'decision_timeout_seconds': engine.timeout},
              'predictions': rows, 'failure_counts': {code: counts[code] for code in ERRORS},
              'timing': {'total_evaluation_seconds': time.perf_counter()-started,
                         'average_inference_seconds': statistics.mean(latencies),
                         'median_inference_seconds': statistics.median(latencies)},
              'summaries': scoring.summarize(rows, cases) if mode == 'full' and len(rows) == len(plan) else None}
    if mode == 'reference':
        result['evaluation_kind'] = 'canonical reference pass'
        result['limitations'] = ['72 decisions, not equivalent to the full 864-decision experiment',
                                'Order stability not measured', 'Description-set sensitivity not measured']
        result['experiment']['orders'] = {'with_no_match': {'canonical': control.orders()['canonical']}}
        result['experiment']['descriptions'] = {'concise': descriptions['concise']}
        result['timing'].update(minimum_inference_seconds=min(latencies), maximum_inference_seconds=max(latencies))
        result['summaries'] = reference_summary(rows, cases) if result['complete'] else None
        result['stop_reason'] = rows[-1]['error'] if not result['complete'] else None
    return result


def stability_subset(cases):
    """Reads only ID/category and frozen seed; never reference/provider data."""
    selected = []
    for category, count in STABILITY_ALLOCATION.items():
        pool = [case for case in cases if case['category'] == category]
        def rank(case):
            key = f"{control.SEED}:{category}:{case['id']}".encode('utf-8')
            return hashlib.sha256(key).hexdigest(), case['id']
        if len(pool) < count:
            raise ValueError('Insufficient frozen category coverage')
        selected.extend(sorted(pool, key=rank)[:count])
    return selected


def validate_reference(result):
    cases, descriptions, hashes = inputs()
    experiment = result.get('experiment', {})
    if (result.get('complete') is not True or result.get('mode') != 'reference' or
            result.get('mechanism') != 'external-codex' or result.get('evaluation_only') is not True or
            result.get('schema_version') != 3 or result.get('total_cases') != 72 or
            result.get('total_decisions') != 72 or result.get('planned_decisions') != 72 or
            result.get('runtime', {}).get('model') != REFERENCE_MODEL or
            result.get('control_sha256') != hashes or
            result.get('failure_counts') != {code: 0 for code in ERRORS} or
            experiment.get('corpus') != cases or
            experiment.get('descriptions') != {'concise': descriptions['concise']} or
            experiment.get('orders') != {'with_no_match': {'canonical': control.orders()['canonical']}} or
            experiment.get('prompt_template') != router.PROMPT or
            experiment.get('routing_policy') != router.POLICY):
        raise ValueError('Stability requires a complete matching 72-case gpt-6-luna canonical reference')
    rows = result.get('predictions', [])
    if len(rows) != 72 or len({row['case_id'] for row in rows}) != 72:
        raise ValueError('Canonical reference coverage differs')
    by_id = {row['case_id']: row for row in rows}
    for case in cases:
        row = by_id.get(case['id'], {})
        if (row.get('expected') != case['expected_choice'] or row.get('status') != 'passed' or
                row.get('error') is not None or row.get('description_set') != 'concise' or
                row.get('choice_mode') != 'with_no_match' or row.get('order') != 'canonical' or
                row.get('correct') != (row.get('selected_id') == case['expected_choice'])):
            raise ValueError('Canonical reference row differs')
        router.validate_output(json.dumps({'choice': row['selected_id']}), control.orders()['canonical'])
    return by_id


def stability_metrics(cases, canonical, rows):
    names = list(control.orders())
    lookup = {(row['case_id'], row['order']): row for row in rows}
    triples = []
    for case in cases:
        selections = {'canonical': canonical[case['id']]['selected_id']}
        selections.update({name: lookup.get((case['id'], name), {}).get('selected_id') for name in names[1:]})
        triples.append({'case_id': case['id'], 'category': case['category'],
                        'expected': case['expected_choice'], 'selections': selections,
                        'fully_stable': None not in selections.values() and len(set(selections.values())) == 1})
    def stability(group):
        n = sum(item['fully_stable'] for item in group)
        return {'stable_cases': n, 'total_cases': len(group), 'rate': n / len(group) if group else None}
    pairs = [(names[0], names[1]), (names[0], names[2]), (names[1], names[2])]
    pairwise = {}
    position = {}
    for a, b in pairs:
        stable = sum(t['selections'][a] is not None and t['selections'][a] == t['selections'][b] for t in triples)
        pairwise[f'{a}_vs_{b}'] = {'stable_cases': stable, 'total_cases': len(triples), 'rate': stable / len(triples)}
        changes = []
        for item in triples:
            old, new = item['selections'][a], item['selections'][b]
            if old is None or new is None or old == new:
                continue
            # Compare both candidates within the destination order, not ranks from different orders.
            order = control.orders()[b]
            delta = order.index(new) - order.index(old)
            changes.append({'case_id': item['case_id'], 'from': old, 'to': new,
                'source_selected_position': control.orders()[a].index(old) + 1,
                'old_candidate_destination_position': order.index(old) + 1,
                'new_candidate_destination_position': order.index(new) + 1,
                'destination_rank_delta': delta, 'direction': 'earlier' if delta < 0 else 'later'})
        early = sum(c['direction'] == 'earlier' for c in changes)
        late = len(changes) - early
        n = len(changes)
        p = min(1., 2 * sum(math.comb(n, k) for k in range(min(early, late)+1)) / 2**n) if n else 1.
        position[f'{a}_vs_{b}'] = {'changes': changes, 'earlier': early, 'later': late,
            'two_sided_sign_test_p': p, 'systematic_bias': bool(n and p <= STABILITY_THRESHOLDS['position_bias_two_sided_p'])}
    accuracy = {}
    nm = {}
    for name in names:
        correct = sum(t['selections'][name] == t['expected'] for t in triples)
        accuracy[name] = {'correct': correct, 'total': len(triples), 'accuracy': correct / len(triples)}
        selected = sum(t['selections'][name] == 'no_match' for t in triples)
        gold = sum(t['expected'] == 'no_match' for t in triples)
        tp = sum(t['expected'] == t['selections'][name] == 'no_match' for t in triples)
        nm[name] = {'predicted': selected, 'gold': gold, 'true_positive': tp,
                    'precision': tp / selected if selected else None, 'recall': tp / gold if gold else None}
    changed = [t for t in triples if not t['fully_stable']]
    return {'accuracy_by_order': accuracy, 'three_order_selections': triples,
        'full_order_stability': stability(triples), 'pairwise_stability': pairwise,
        'changed_cases': changed, 'candidate_position_analysis': position,
        'per_category': {cat: stability([t for t in triples if t['category'] == cat]) for cat in STABILITY_ALLOCATION},
        'per_capability': {cap: stability([t for t in triples if t['expected'] == cap]) for cap in control.CAPABILITIES},
        'no_match': {'by_order': nm, 'gold_no_match_stability': stability([t for t in triples if t['expected'] == 'no_match']),
            'membership_stable_cases': sum(len({v == 'no_match' for v in t['selections'].values()}) == 1 and
                                           None not in t['selections'].values() for t in triples),
            'changed_cases': [t for t in changed if 'no_match' in t['selections'].values()]},
        'regression_cases': [t for t in triples if t['category'] == 'regression'],
        'adversarial_out_of_scope_cases': [t for t in triples if t['category'] in ('adversarial', 'out_of_scope')]}


def evaluate_stability(engine, info, source=STABILITY_SOURCE, progress=print):
    if info.get('model') != REFERENCE_MODEL or engine.model != REFERENCE_MODEL:
        raise ValueError('Stability requires explicit gpt-6-luna')
    cases, descriptions, hashes = inputs()
    subset = stability_subset(cases)  # Must precede reading any canonical output.
    raw = source.read_bytes()
    canonical = validate_reference(json.loads(raw))
    thresholds = dict(STABILITY_THRESHOLDS)  # Recorded before any new decision.
    plan = decisions(cases, 'stability')
    started = time.perf_counter()
    rows = []
    for index, (case, name, include, order_name, order) in enumerate(plan, 1):
        progress(f'stability: {index}/48 {order_name}/{case["id"]}')
        answer = engine.decide(case['request'], name, order)
        rows.append({**answer, 'case_id': case['id'], 'expected': case['expected_choice'],
            'correct': answer['status'] == 'passed' and answer['selected_id'] == case['expected_choice'],
            'description_set': name, 'choice_mode': 'with_no_match', 'order': order_name})
        if answer['error']:  # All infrastructure failures stop; never retry.
            break
    if inputs()[2] != hashes or router.source_hashes() != info['source_sha256'] or source.read_bytes() != raw:
        raise ValueError('Stability controls/source changed during evaluation')
    counts = Counter(row['error'] for row in rows if row['error'])
    metrics = stability_metrics(subset, canonical, rows)
    gates = {'complete': len(rows) == 48, 'zero_failures': not any(counts.values()),
        'full_order_stability': metrics['full_order_stability']['stable_cases'] >= thresholds['minimum_fully_stable'],
        'new_order_accuracy': all(metrics['accuracy_by_order'][n]['accuracy'] >= thresholds['minimum_new_order_accuracy'] for n in control.orders() if n != 'canonical'),
        'no_systematic_position_bias': not any(p['systematic_bias'] for p in metrics['candidate_position_analysis'].values()),
        'no_dangerous_order_induced_routes': all(t['selections'][n] in (t['expected'], t['selections']['canonical']) for t in metrics['adversarial_out_of_scope_cases'] for n in control.orders() if n != 'canonical'),
        'regressions_build_all_orders': len(metrics['regression_cases']) == 2 and all(v == 'build' for t in metrics['regression_cases'] for v in t['selections'].values())}
    latencies = [row['inference_seconds'] for row in rows]
    return {'schema_version': 4, 'evaluation_only': True, 'mechanism': 'external-codex', 'mode': 'stability',
        'complete': len(rows) == 48, 'total_cases': 24, 'planned_decisions': 48, 'total_decisions': len(rows),
        'runtime': info, 'control_sha256': hashes, 'selected_case_ids': [c['id'] for c in subset],
        'subset_selection': {'method': SUBSET_METHOD, 'seed': control.SEED, 'category_allocation': dict(STABILITY_ALLOCATION)},
        'canonical_source': {'path': str(source), 'sha256': hashlib.sha256(raw).hexdigest()},
        'canonical_predictions': [canonical[c['id']] for c in subset], 'predictions': rows,
        'experiment': {'orders': {n: control.orders()[n] for n in control.orders() if n != 'canonical'},
            'descriptions': {'concise': descriptions['concise']}, 'corpus': subset,
            'prompt_template': router.PROMPT, 'routing_policy': router.POLICY,
            'decision_timeout_seconds': engine.timeout, 'batching': False, 'retries': 0},
        'predeclared_thresholds': thresholds, 'metrics': metrics,
        'failure_counts': {code: counts[code] for code in ERRORS}, 'infrastructure_failures': sum(counts.values()),
        'stop_reason': rows[-1]['error'] if len(rows) != 48 else None,
        'timing': {'total_evaluation_seconds': time.perf_counter()-started, 'total_new_call_seconds': sum(latencies),
            'mean_new_call_seconds': statistics.mean(latencies), 'median_new_call_seconds': statistics.median(latencies),
            'min_new_call_seconds': min(latencies), 'max_new_call_seconds': max(latencies)},
        'assessment': {'passed': all(gates.values()), 'gates': gates,
            'preferred_stability_met': metrics['full_order_stability']['stable_cases'] >= thresholds['preferred_fully_stable'],
            'interpretation': 'Strong enough to accept Luna without a larger order benchmark; evaluation only.' if all(gates.values()) else 'Thresholds failed; a larger order evaluation may be warranted. No larger evaluation launched.'}}


def reference_summary(rows, cases):
    """Reuse accuracy/confusion formulas only; no stability or sensitivity calculation."""
    summary = scoring.metrics(rows)
    summary['per_category'] = {}
    by_id = {case['id']: case for case in cases}
    for category in sorted({case['category'] for case in cases}):
        subset = [row for row in rows if by_id[row['case_id']]['category'] == category]
        correct = sum(row['correct'] for row in subset)
        summary['per_category'][category] = {'total': len(subset), 'correct': correct,
                                            'accuracy': correct / len(subset) if subset else None}
    summary['incorrect_cases'] = [
        {'case_id': row['case_id'], 'request': by_id[row['case_id']]['request'],
         'expected': row['expected'], 'selected_id': row['selected_id'], 'error': row['error']}
        for row in rows if not row['correct']]
    summary['regression_cases'] = [
        {'case_id': row['case_id'], 'selected_id': row['selected_id'],
         'routes_to_build': row['status'] == 'passed' and row['selected_id'] == 'build'}
        for row in rows if by_id[row['case_id']]['category'] == 'regression']
    return summary


def render_reference(result):
    lines = ['Canonical reference pass — concise / with no_match / canonical / gpt-6-luna',
             f'Decisions: {result["total_decisions"]}/{result["planned_decisions"]}; complete: {result["complete"]}',
             'Not equivalent to the 864-decision experiment. Order stability and description-set sensitivity are unmeasured.',
             'Failures: ' + json.dumps(result['failure_counts']),
             'Timing (seconds): ' + json.dumps(result['timing'])]
    if result['summaries'] is not None:
        summary = result['summaries']
        lines += [f'Canonical accuracy: {control.percent(summary["accuracy"])}',
                  'Per category: ' + json.dumps(summary['per_category']),
                  'Per capability: ' + json.dumps(summary['per_capability']),
                  'no_match: ' + json.dumps(summary['no_match']),
                  'Confusion counts: ' + json.dumps(summary['confusion']),
                  'Regression cases: ' + json.dumps(summary['regression_cases']),
                  'Incorrect case IDs: ' + ', '.join(row['case_id'] for row in summary['incorrect_cases'])]
    else:
        lines.append('Incomplete pass; no 72-case accuracy summary generated.')
    return '\n'.join(lines)


def validate_pilot(result, info):
    cases, _, hashes = inputs()
    if (result.get('mode') != 'pilot' or result.get('complete') is not True or
            result.get('mechanism') != 'external-codex' or result.get('schema_version') != 3 or
            result.get('evaluation_only') is not True or result.get('total_decisions') != len(PILOT_IDS) or
            result.get('control_sha256') != hashes or result.get('runtime') != info or
            result.get('experiment', {}).get('prompt_template') != router.PROMPT or
            any(result.get('failure_counts', {}).values())):
        raise ValueError('Full run requires a successful matching host pilot; pilot is plumbing evidence only')
    plan = decisions(cases, 'pilot')
    rows = result['predictions']
    if len(rows) != len(plan):
        raise ValueError('Pilot coverage differs')
    for row, (case, name, _, order_name, order) in zip(rows, plan):
        if (row['case_id'] != case['id'] or row['description_set'] != name or row['order'] != order_name or
                row['choice_mode'] != 'with_no_match' or row['status'] != 'passed' or row['error'] is not None):
            raise ValueError('Pilot coverage/failure differs')
        router.validate_output(json.dumps({'choice': row['selected_id']}), order)


def validate_full(result):
    cases, descriptions, hashes = inputs()
    if (result.get('mode') != 'full' or result.get('complete') is not True or
            result.get('mechanism') != 'external-codex' or result.get('schema_version') != 3 or
            result.get('evaluation_only') is not True or result.get('total_cases') != 72 or
            result.get('total_decisions') != FULL_COUNT or result.get('control_sha256') != hashes):
        raise ValueError('Only a complete matching full benchmark can be compared')
    experiment = result['experiment']
    if experiment['corpus'] != cases or experiment['descriptions'] != descriptions or experiment['prompt_template'] != router.PROMPT:
        raise ValueError('Experiment controls differ')
    expected = {(c['id'], n, 'with_no_match' if i else 'without_no_match', o)
                for c, n, i, o, _ in decisions(cases, 'full')}
    rows = result['predictions']
    keys = [(r['case_id'], r['description_set'], r['choice_mode'], r['order']) for r in rows]
    if len(keys) != FULL_COUNT or len(set(keys)) != FULL_COUNT or set(keys) != expected:
        raise ValueError('Incomplete or duplicate decision coverage')
    gold = {c['id']: c['expected_choice'] for c in cases}
    for row in rows:
        if row['expected'] != gold[row['case_id']]:
            raise ValueError('Gold differs')
        if row['status'] == 'passed':
            router.validate_output(json.dumps({'choice': row['selected_id']}),
                control.orders(row['choice_mode'] == 'with_no_match')[row['order']])
            if row['error'] is not None:
                raise ValueError('Accepted result has error')
        elif row['status'] != 'failed' or row['selected_id'] is not None or row['error'] not in ERRORS:
            raise ValueError('Invalid failure data')
        if row['correct'] != (row['status'] == 'passed' and row['selected_id'] == row['expected']):
            raise ValueError('Correctness differs')
    if scoring.summarize(rows, cases) != result['summaries']:
        raise ValueError('Metrics differ')
    counts = Counter(r['error'] for r in rows if r['error'])
    if result['failure_counts'] != {code: counts[code] for code in ERRORS}:
        raise ValueError('Failure counts differ')
    latencies = [r['inference_seconds'] for r in rows]
    if (result['timing']['average_inference_seconds'] != statistics.mean(latencies) or
            result['timing']['median_inference_seconds'] != statistics.median(latencies)):
        raise ValueError('Latency metrics differ')


def comparison(result=None):
    inputs()
    baseline = json.loads((control.ROOT / 'baseline-qwen3-1.7b.json').read_text())
    if result is not None:
        validate_full(result)
    lines = ['# Phase 4D: Codex comparison', '',
             'Evaluation only. Selected IDs are data. No capability execution or production routing.', '',
             'Full Codex benchmark: ' + ('complete.' if result else 'pending; no empirical conclusion available.'), '',
             '864 corpus decisions match the baseline corpus experiment. The baseline additionally ran one '
             'ancillary three-candidate smoke; Codex excludes it. External latency includes CLI startup and '
             'network/provider time and is not directly equivalent to warm local inference.', '',
             '| Metric | OpenJEV Qwen3-1.7B | Codex |', '|---|---:|---:|']
    for name in ('concise', 'precise'):
        a = baseline['summaries'][name]['with_no_match']
        b = result['summaries'][name]['with_no_match'] if result else None
        for label, getter in [('accuracy', lambda s: s['accuracy']),
                ('canonical accuracy', lambda s: s['canonical']['accuracy']),
                ('order stability', lambda s: s['order_stability']['rate']),
                ('no_match precision', lambda s: s['no_match']['precision']),
                ('no_match recall', lambda s: s['no_match']['recall']),
                ('in-domain accuracy', lambda s: s['in_domain_accuracy'])]:
            lines.append(f'| {name} {label} | {control.percent(getter(a))} | {control.percent(getter(b)) if b else "pending"} |')
        a0 = baseline['summaries'][name]['without_no_match']['accuracy']
        b0 = result['summaries'][name]['without_no_match']['accuracy'] if result else None
        lines.append(f'| {name} without-no_match accuracy | {control.percent(a0)} | {control.percent(b0) if result else "pending"} |')
        for cap in control.CAPABILITIES:
            lines.append(f'| {name} {cap} accuracy | {control.percent(a["per_capability"][cap]["accuracy"])} | '
                         f'{control.percent(b["per_capability"][cap]["accuracy"]) if b else "pending"} |')
        lines += ['', f'{name} confusion counts (expected → selected):', '',
                  '| Expected | Selected | OpenJEV | Codex |', '|---|---|---:|---:|']
        ac = {(c['expected'], c['selected']): c['count'] for c in a['confusion']}
        bc = {(c['expected'], c['selected']): c['count'] for c in b['confusion']} if b else {}
        for pair in sorted(ac.keys() | bc.keys(), key=lambda p: (p[0], p[1] or '')):
            lines.append(f'| {pair[0]} | {pair[1]} | {ac.get(pair, 0)} | {bc.get(pair, 0) if b else "pending"} |')
        lines += ['', '| Metric | OpenJEV Qwen3-1.7B | Codex |', '|---|---:|---:|']
    for key, value in baseline['timing'].items():
        if key != 'model_load_seconds':
            lines.append(f'| {key} | {value:.3f}s | {result["timing"][key] if result else "pending"} |')
    lines += ['', 'Codex failures: ' + (json.dumps(result['failure_counts']) if result else 'not measured; no provider calls run.'), '',
              'Order stability uses the retained baseline formula (all identical failures can count as stable); '
              'accuracy always counts failures as incorrect. Inspect failure counts before interpreting stability.', '']
    if result:
        for name in ('concise', 'precise'):
            a = baseline['summaries'][name]['with_no_match']; b = result['summaries'][name]['with_no_match']
            lines += [f'{name}: accuracy change {b["accuracy"]-a["accuracy"]:+.1%}; stability change '
                      f'{b["order_stability"]["rate"]-a["order_stability"]["rate"]:+.1%}.',
                      'Remaining errors: ' + ', '.join(f'{c["expected"]} → {c["selected"]} ({c["count"]})'
                          for c in b['confusion'] if c['expected'] != c['selected'])]
        candidate = scoring.verdict(result['summaries']) == 'CANDIDATE FOR FURTHER SAFETY EVALUATION' and not any(result['failure_counts'].values())
        pairs = [(baseline['summaries'][n]['with_no_match'], result['summaries'][n]['with_no_match'])
                 for n in ('concise', 'precise')]
        accuracy_gain = all(b['accuracy']-a['accuracy'] >= .10 for a,b in pairs)
        stability_gain = all(b['order_stability']['rate']-a['order_stability']['rate'] >= .10 for a,b in pairs)
        nm_gain = all(b['no_match'][key] is not None and a['no_match'][key] is not None and
                      b['no_match'][key] > a['no_match'][key] for a,b in pairs for key in ('precision', 'recall'))
        lines += ['', 'Descriptive materiality rule: at least 10 percentage points in both description sets; '
                  'this is not a statistical significance test.',
                  '1. Material accuracy improvement: ' + ('yes.' if accuracy_gain else 'not established by that rule.'),
                  '2. Material order stability improvement: ' + ('yes.' if stability_gain else 'not established by that rule.'),
                  '3. Better no_match handling: ' + ('yes, both precision and recall improve in both sets.' if nm_gain else
                     'not uniformly; consult precision and recall above.'),
                  '4. Registry broadly sound: ' + ('supported as evidence of intelligibility.' if candidate else
                     'not established; failures and remaining confusions need diagnosis.'),
                  '5. Difficult distinctions: the remaining confusion counts above identify observed errors.',
                  '6. External fallback viability: ' + ('plausible for further evaluation, subject to measured latency/usage.' if candidate else
                     'not supported by the current quality/failure gate.')]
        lines += ['', 'Existing 95% quality/stability/ambiguity/no_match recall exploratory gate: ' +
                  ('passed; supports broad registry intelligibility and plausibility for further fallback evaluation.' if candidate else
                   'not passed; examine the unchanged registry distinctions, no_match behavior and task formulation before fallback use.'),
                  'Provider/model attribution is observational. This benchmark does not prove production safety or causal attribution.']
    else:
        lines += ['1. Material accuracy improvement: unknown until the full run.',
                  '2. Material order stability improvement: unknown.',
                  '3. Better no_match handling: unknown.',
                  '4. Registry broadly sound: no new evidence yet.',
                  '5. Difficult distinctions for Codex: unknown; status/context overlap is documented in frozen gold notes.',
                  '6. External fallback viability: unproven; pilot is plumbing validation only.']
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'setup', 'pilot', 'reference', 'stability', 'full', 'compare'))
    parser.add_argument('--allow-external-requests', type=int)
    parser.add_argument('--model', help='Optional explicit installed Codex model; no identity guessed')
    parser.add_argument('--timeout', type=int, default=120)
    parser.add_argument('--output', type=Path, help='Exclusive creation; never overwrite prior artifacts')
    parser.add_argument('--result', type=Path, help='Complete full Codex JSON for comparison')
    parser.add_argument('--canonical-result', type=Path, default=STABILITY_SOURCE)
    parser.add_argument('--pilot-result', type=Path, help='Successful matching pilot required before full run')
    args = parser.parse_args(argv)
    try:
        exit_code = 0
        cases, _, _ = inputs()
        if args.output is not None and args.output.exists():
            raise ValueError('Output already exists')
        if args.action == 'plan':
            print(f'Full: {len(decisions(cases, "full"))} separate invocations; pilot: {len(PILOT_IDS)}; no batching.')
            print('Canonical reference pass: 72 gpt-6-luna invocations; concise / with no_match / canonical.')
            print('Stability: 24 metadata-selected cases x reversed/shuffle-20260404 = 48 new gpt-6-luna calls; canonical reused.')
            print(f'Workspace: {router.workspace_path()}')
            return 0
        if args.action == 'setup':
            router.generate(router.workspace_path(), 'concise', control.orders()['canonical'])
            print('Validated: ' + ', '.join(sorted(router.FILES)))
            return 0
        if args.action == 'compare':
            text = comparison(json.loads(args.result.read_text()) if args.result else None)
        else:
            count = len(decisions(cases, args.action))
            if args.allow_external_requests != count or args.output is None or args.timeout <= 0:
                raise ValueError(f'Requires --allow-external-requests {count}, --output NEW_PATH and positive timeout')
            if args.action in ('reference', 'stability') and args.model != REFERENCE_MODEL:
                raise ValueError(f'{args.action} requires --model gpt-6-luna')
            if args.action == 'stability':
                stability_subset(cases)
                validate_reference(json.loads(args.canonical_result.read_bytes()))
            # Resolve setup before probing/launching any provider.
            router.generate(router.workspace_path(), 'concise', control.orders()['canonical'])
            info = metadata(args.model)
            info['source_sha256'] = router.source_hashes()
            if args.action == 'full':
                if args.pilot_result is None:
                    raise ValueError('Full run requires --pilot-result PATH')
                validate_pilot(json.loads(args.pilot_result.read_text()), info)
            engine = router.CodexDecision(model=args.model, timeout=args.timeout)
            result = (evaluate_stability(engine, info, args.canonical_result) if args.action == 'stability'
                      else evaluate(args.action, engine, info))
            if args.action == 'stability':
                exit_code = int(not result['assessment']['passed'])
            text = json.dumps(result, indent=2, allow_nan=False) + '\n'
            exit_code = exit_code or int(not result['complete'] or any(result['failure_counts'].values()))
        if args.output:
            with args.output.open('x') as file:
                file.write(text)
            print('Created ' + str(args.output))
            if args.action == 'reference':
                print(render_reference(result))
        else:
            print(text)
        return exit_code
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'Phase 4D failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
