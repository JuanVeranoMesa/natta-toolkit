"""Read-only mechanism comparison, including explicitly pending host evaluations."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare
import evaluate as control
import evaluate_selection as generation
from selection import SYSTEM, REASONING, USER_TEMPLATE, DECODE, FINAL_TOKENS, THINK_TOKENS

OUTPUT = control.ROOT/'COMPARISON-SELECTION-MECHANISMS.md'


def load(mode, baseline):
    path = control.ROOT/f'baseline-qwen3-1.7b-{mode}.json'
    if not path.exists():
        return None
    result = json.loads(path.read_text())
    if (result['schema_version'] != 2 or result['evaluation_only'] is not True or
            result['mechanism'] != mode or result['total_cases'] != 72 or result['total_decisions'] != 865):
        raise ValueError('Incomplete generation evaluation')
    for key in ('model_id', 'model_revision', 'model_path', 'backend', 'dtype', 'offline', 'python_version', 'uv_lock_sha256'):
        if result['runtime'][key] != baseline['runtime'][key]:
            raise ValueError('Generation runtime differs: '+key)
    experiment = result['experiment']
    for key in ('seed', 'orders', 'descriptions', 'corpus'):
        if experiment[key] != baseline['experiment'][key]:
            raise ValueError('Generation control differs: '+key)
    expected_config = {'system_template': SYSTEM + (REASONING if mode == 'reasoning' else ''),
        'user_template': USER_TEMPLATE, 'enable_thinking': mode == 'reasoning', 'decoding': DECODE,
        'final_max_new_tokens': FINAL_TOKENS, 'thinking_max_new_tokens': THINK_TOKENS if mode == 'reasoning' else 0,
        'decision_timeout_seconds': 90, 'evaluation_timeout_seconds': generation.LIMITS[mode], 'prompt_max_tokens': 4096}
    if any(experiment[k] != v for k, v in expected_config.items()):
        raise ValueError('Generation configuration differs')
    if result['control_sha256'] != generation.verify_controls():
        raise ValueError('Frozen evidence identity differs')
    expected = {(c['id'], d, m, o) for c in experiment['corpus'] for d in experiment['descriptions']
                for m in ('with_no_match', 'without_no_match') for o in control.orders(m == 'with_no_match')}
    rows = result['predictions']
    keys = [(r['case_id'], r['description_set'], r['choice_mode'], r['order']) for r in rows]
    if len(keys) != 864 or len(set(keys)) != 864 or set(keys) != expected:
        raise ValueError('Incomplete or duplicate corpus coverage')
    gold = {c['id']: c['expected_choice'] for c in experiment['corpus']}
    for row in rows:
        candidates = control.orders(row['choice_mode'] == 'with_no_match')[row['order']]
        if row['expected'] != gold[row['case_id']]:
            raise ValueError('Gold label differs')
        validate_answer(row, candidates)
        if row['correct'] != (row['status'] == 'passed' and row['selected_id'] == row['expected']):
            raise ValueError('Correctness differs')
    if result['legacy_smoke']['request'] != control.SMOKE:
        raise ValueError('Smoke request differs')
    validate_answer(result['legacy_smoke']['answer'], control.SMOKE['choices'])
    if generation.summarize(rows, experiment['corpus']) != result['summaries']:
        raise ValueError('Generation metrics differ')
    if generation.verdict(result['summaries']) != result['verdict']:
        raise ValueError('Generation verdict differs')
    return result


def validate_answer(row, candidates):
    if row['status'] == 'passed':
        if row['selected_id'] not in candidates or row['error'] is not None:
            raise ValueError('Invalid accepted selection')
    elif row['status'] != 'failed' or row['selected_id'] is not None or row['error'] not in ('malformed_output', 'unknown_choice'):
        raise ValueError('Invalid failure boundary')
    if any(k in row for k in ('concentration', 'probabilities', 'reasoning', 'thinking_text')):
        raise ValueError('Unapproved confidence or reasoning persistence')


def report(a, b=None, c=None):
    results = [('OpenJEV', a), ('Structured', b), ('Reasoning', c)]
    fmt = control.percent
    lines = ['# Qwen3-1.7B selection-mechanism comparison', '',
        'Evaluation only. No capability execution authority; production routing remains disabled.', '',
        'OpenJEV remains installed as an evaluated decision mechanism. Qwen3-0.6B + OpenJEV and '
        'Qwen3-1.7B + OpenJEV are REJECTED FOR ROUTING.', '',
        'Host status: '+('both generation benchmarks complete.' if b and c else
        'generation evaluation pending or incomplete. This Codex session has MPS built but unavailable; no CPU fallback was used. '
        'This is not a real-host failure.'), '',
        'Fixed controls: 72 cases, 864 corpus decisions plus one ancillary smoke, concise/precise descriptions, '
        'canonical/reversed/shuffle-20260404 orders, peer no_match, and the same with/without-no_match runs.', '',
        '| Metric | OpenJEV | Structured generation | Reasoning + structured final |',
        '|---|---:|---:|---:|']
    def row(label, getter, percent=True):
        values = []
        for _, result in results:
            value = getter(result) if result else None
            values.append((fmt(value) if percent else str(value)) if result else 'pending')
        lines.append('| '+label+' | '+' | '.join(values)+' |')
    for d in ('concise', 'precise'):
        for label, getter in [('accuracy', lambda s: s['accuracy']),
                ('canonical accuracy', lambda s: s['canonical']['accuracy']),
                ('order stability', lambda s: s['order_stability']['rate']),
                ('no_match precision', lambda s: s['no_match']['precision']),
                ('no_match recall', lambda s: s['no_match']['recall']),
                ('in-domain accuracy', lambda s: s['in_domain_accuracy'])]:
            row(d+' / '+label, lambda r, d=d, getter=getter: getter(r['summaries'][d]['with_no_match']))
        row(d+' / without-no_match accuracy', lambda r, d=d: r['summaries'][d]['without_no_match']['accuracy'])
        for cap in control.CAPABILITIES:
            row(d+' / '+cap, lambda r, d=d, cap=cap: r['summaries'][d]['with_no_match']['per_capability'][cap]['accuracy'])
        row(d+' / malformed rate', lambda r, d=d: r['summaries'][d]['with_no_match'].get('malformed_output_rate'))
        row(d+' / malformed count', lambda r, d=d: r['summaries'][d]['with_no_match'].get('malformed_output_count', 'n/a'), False)
        row(d+' / unknown count', lambda r, d=d: r['summaries'][d]['with_no_match'].get('unknown_choice_count', 'n/a'), False)
    for key in ('model_load_seconds', 'total_evaluation_seconds', 'average_inference_seconds', 'median_inference_seconds',
                'total_generated_tokens', 'average_generated_tokens_per_decision'):
        row(key, lambda r, key=key: r['timing'].get(key, 'n/a'), False)
    lines += ['', 'Concentration/confidence: not applicable for structured-generation mode (including reasoning). '
              'OpenJEV concentration is uncalibrated and is retained only in the unchanged control.', '',
              'OpenJEV compilation regression: build, concentration 0.9997. This improves one semantic example '
              'without overcoming the aggregate accuracy and order-stability failures.', '']
    for label, result in results:
        lines += [f'## {label}', '', 'Verdict: '+(('REJECTED FOR ROUTING' if label == 'OpenJEV' else result['verdict']) if result else 'NOT EVALUATED'), '']
        if result:
            for d in ('concise', 'precise'):
                s = result['summaries'][d]['with_no_match']
                errors = sorted([x for x in s['confusion'] if x['expected'] != x['selected']],
                                key=lambda x: (-x['count'], x['expected'], x['selected'] or ''))
                nm = sum(x['count'] for x in errors if x['expected'] == 'no_match' or x['selected'] == 'no_match')
                lines += [f'{d}: no_match-related errors {nm}/{s["incorrect"]}. Confusions: '+json.dumps(errors), '',
                          'Per-capability counts: '+json.dumps(s['per_capability']), '']
    lines += ['## Experimental questions', '',
        'Material improvement is described using a fixed exploratory rule: at least +5 percentage points '
        'in both description sets. It is not a significance test or production gate.', '']
    def improved(x, y, metric):
        return all(metric(y['summaries'][d]['with_no_match'])-metric(x['summaries'][d]['with_no_match']) >= .05
                   for d in ('concise', 'precise'))
    answers = [
        ('Does structured generation materially outperform OpenJEV?',
         ('Yes' if improved(a,b,lambda s:s['accuracy']) else 'No under the fixed exploratory rule') if b else 'Pending host benchmark'),
        ('Does it materially improve order stability?',
         ('Yes' if improved(a,b,lambda s:s['order_stability']['rate']) else 'No under the fixed exploratory rule') if b else 'Pending host benchmark'),
        ('Does reasoning materially improve over structured generation?',
         ('Yes' if improved(b,c,lambda s:s['accuracy']) else 'No under the fixed exploratory rule') if b and c else 'Pending both host benchmarks')]
    for question, answer in answers:
        lines += [f'{question} **{answer}.**', '']
    if b and c:
        ratio = c['timing']['average_inference_seconds']/b['timing']['average_inference_seconds']
        utility = ('Material correctness gains warrant further latency/safety study; deployment utility remains unproven.'
                   if improved(b,c,lambda s:s['accuracy']) else
                   'The fixed comparison does not show material correctness gains to justify that cost.')
        lines += [f'Does reasoning justify its latency cost? Mean latency ratio is {ratio:.2f}x. '+utility, '']
    else:
        lines += ['Does reasoning justify its latency cost? Pending measured correctness, stability and latency; no claim is possible.', '']
    if b and c:
        counts = []
        for label, result in [('Structured', b), ('Reasoning', c)]:
            for d in ('concise', 'precise'):
                summary = result['summaries'][d]['with_no_match']
                count = sum(x['count'] for x in summary['confusion'] if x['expected'] != x['selected'] and
                            (x['expected'] == 'no_match' or x['selected'] == 'no_match'))
                counts.append(f'{label}/{d}: {count}/{summary["incorrect"]} errors involve no_match')
        nm_answer = '; '.join(counts)+'. Dominant here means more than half of all errors.'
    else:
        nm_answer = 'Control counts above show its contribution; whether it remains dominant in generation requires both completed results.'
    lines += ['Is no_match still a dominant source of failure? '+nm_answer, '',
              'Does evidence now implicate the registry/taxonomy itself? No new causal conclusion is warranted. '
              'Status/context overlap is already documented, but mechanism errors and ambiguous labels remain confounded. '
              'Repeated errors across mechanisms can motivate an independent taxonomy audit; they cannot prove taxonomy is the cause.', '',
              'Next experiment: complete this fixed comparison first, then independently audit recurring confusions and '
              'ambiguous gold labels. A separately frozen two-stage routability/selection experiment may follow; '
              'do not redesign no_match during Phase 4C.', '',
              'Configuration, exact templates, boundaries, source links and Ghostty commands: [PHASE4C.md](PHASE4C.md).', '']
    return '\n'.join(lines)


def main():
    generation.verify_controls()
    a = compare.load_result(control.ROOT/'baseline-qwen3-1.7b.json', 'qwen3-1.7b')
    b, c = load('structured', a), load('reasoning', a)
    OUTPUT.write_text(report(a, b, c))  # Only this new report is regenerated; baselines are exclusive writes.
    print(f'Comparison: {OUTPUT}')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as exc:
        print(f'Comparison failed: {exc}', file=sys.stderr)
        sys.exit(1)
