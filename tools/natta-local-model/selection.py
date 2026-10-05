"""Phase 4C generation-only decisions. No dispatcher, handlers, or execution API."""
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from decision import validate_request
import local_model as contract

PROFILE = 'qwen3-1.7b'
STACK = {'openjev': '0.1.0', 'torch': '2.14.1', 'transformers': '5.18.0', 'huggingface-hub': '1.33.0'}
SYSTEM = ('Select the single offered Natta operation that best matches the requested outcome. '
          'Choose only the capability; do not extract project names or arguments. '
          'If no_match is offered, select it for insufficient intent or requests outside the offered operations. '
          'Return a choice as data only; do not execute anything. '
          'The final answer must be exactly one JSON object with only the key "choice", '
          'whose value is one supplied candidate ID.')
REASONING = (' Reason about the requested intent and differences between candidates in your native '
             '<think> block before returning the final JSON answer.')
USER_TEMPLATE = 'Request and candidates (JSON data):\n{payload}'
DECODE = dict(do_sample=False, num_beams=1, num_return_sequences=1, use_cache=True,
              repetition_penalty=1.0, temperature=1.0, top_p=1.0, top_k=50,
              forced_bos_token_id=None, forced_eos_token_id=None)
FINAL_TOKENS = 32
THINK_TOKENS = 1024
DECISION_SECONDS = 90


class OutputFailure(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def validate_output(text, choices):
    """Whole JSON parsing, duplicate-key detection, no prose extraction or execution."""
    def pairs(items):
        if len({k for k, _ in items}) != len(items):
            raise OutputFailure('malformed_output')
        return dict(items)
    try:
        answer = json.loads(text, object_pairs_hook=pairs)
    except (json.JSONDecodeError, TypeError):
        raise OutputFailure('malformed_output') from None
    if (not isinstance(answer, dict) or set(answer) != {'choice'} or
            not isinstance(answer['choice'], str)):
        raise OutputFailure('malformed_output')
    if answer['choice'] not in choices:
        raise OutputFailure('unknown_choice')
    return answer['choice']


def messages(request, choices, mode):
    if mode not in ('structured', 'reasoning'):
        raise ValueError('Unknown selection mechanism')
    validate_request(request, SYSTEM, choices)
    payload = json.dumps({'request': request, 'candidates': [
        {'id': k, 'description': v} for k, v in choices.items()]}, ensure_ascii=False)
    return [{'role': 'system', 'content': SYSTEM + (REASONING if mode == 'reasoning' else '')},
            {'role': 'user', 'content': USER_TEMPLATE.format(payload=payload)}]


class FinalConstraint:
    """Finite token-prefix language: one complete known-ID JSON object, then EOS."""
    def __init__(self, tokenizer, choices, prompt_length):
        self.prompt_length = prompt_length
        self.eos = tokenizer.eos_token_id
        self.prefixes = {}
        for key in choices:
            text = json.dumps({'choice': key}, separators=(',', ':'))
            tokens = tokenizer.encode(text, add_special_tokens=False)
            if tokenizer.decode(tokens, skip_special_tokens=False) != text:
                raise ValueError('Final JSON tokenization does not round trip')
            if len(tokens) + 1 > FINAL_TOKENS:
                raise ValueError('Final JSON exceeds fixed token bound')
            for index, token in enumerate([*tokens, self.eos]):
                self.prefixes.setdefault(tuple(tokens[:index]), set()).add(token)

    def allowed(self, prefix):
        values = self.prefixes.get(tuple(prefix))
        if not values:
            raise OutputFailure('malformed_output')
        # Numeric sort makes tie handling independent of candidate insertion order.
        return sorted(values)

    def __call__(self, batch_id, ids):
        return self.allowed(ids[self.prompt_length:].tolist())


def reasoning_boundary(tokens, opening, closing, eos):
    """Consume only native token framing, never a choice embedded in reasoning."""
    if (not tokens or tokens[0] != opening or tokens[-1] != closing or
            tokens.count(opening) != 1 or tokens.count(closing) != 1 or eos in tokens):
        raise OutputFailure('malformed_output')
    return len(tokens)


class GenerationDecision:
    def __init__(self, mode):
        messages('validate', {'a': 'A', 'b': 'B'}, mode)
        self.mode = mode
        self.profile = PROFILE
        self.model_path = contract.model_path(profile=PROFILE)
        self.model = self.tokenizer = None
        self.load_seconds = 0.0

    def load(self):
        if self.model is not None:
            return
        started = time.perf_counter()
        os.environ.update({k: v for k, v in contract.offline_environment().items()
                           if k.startswith(('HF_', 'TRANSFORMERS_', 'PYTHONDONTWRITE'))})
        os.environ['PYTORCH_ENABLE_MPS_FALLBACK'] = '0'
        contract.validate_config(contract.runtime_path())
        contract.validate_model(self.model_path, PROFILE)
        from probe import inspect
        metadata = inspect()  # Metadata only; OpenJEV is not imported or used.
        if metadata['versions'] != STACK or metadata['python'] != [3, 12, 15]:
            raise ValueError('Installed stack differs from retained control; stop')
        import torch
        if not torch.backends.mps.is_available():
            raise RuntimeError('PyTorch MPS unavailable in this session; no CPU fallback')
        from transformers import AutoModelForCausalLM, AutoTokenizer
        self.tokenizer = AutoTokenizer.from_pretrained(str(self.model_path), local_files_only=True,
                                                      trust_remote_code=False)
        self.model = AutoModelForCausalLM.from_pretrained(
            str(self.model_path), local_files_only=True, trust_remote_code=False,
            dtype=torch.float32).to('mps').eval()
        if any(p.device.type != 'mps' or p.dtype != torch.float32 for p in self.model.parameters()):
            raise RuntimeError('Model did not load on required MPS/FP32 backend')
        torch.mps.synchronize()
        self.load_seconds = time.perf_counter() - started

    def decide(self, request, choices):
        prompt_messages = messages(request, choices, self.mode)
        self.load()
        import torch
        from transformers import GenerationConfig, StoppingCriteria, StoppingCriteriaList
        tokenizer = self.tokenizer
        text = tokenizer.apply_chat_template(prompt_messages, tokenize=False,
                                              add_generation_prompt=True,
                                              enable_thinking=self.mode == 'reasoning')
        inputs = tokenizer(text, add_special_tokens=False, return_tensors='pt').to('mps')
        if inputs['input_ids'].shape[1] > 4096:
            raise ValueError('Prompt exceeds 4096 tokens; no truncation')
        torch.mps.synchronize()
        started = time.perf_counter()
        deadline = started + DECISION_SECONDS
        generated = thinking = 0
        selected = None
        error = None
        close = tokenizer.encode('</think>', add_special_tokens=False)
        opening = tokenizer.encode('<think>', add_special_tokens=False)
        if len(close) != 1 or len(opening) != 1:
            raise ValueError('Native thinking boundary must be single special tokens')
        class Stop(StoppingCriteria):
            def __init__(self, stop_token=None):
                self.stop_token = stop_token
            def __call__(self, input_ids, scores, **kwargs):
                if time.perf_counter() >= deadline:
                    raise RuntimeError('90-second decision safety timeout; no partial success')
                return self.stop_token is not None and input_ids[0, -1].item() == self.stop_token
        def config(maximum):
            return GenerationConfig(**DECODE, max_new_tokens=maximum,
                                    eos_token_id=tokenizer.eos_token_id,
                                    pad_token_id=tokenizer.eos_token_id)
        try:
            with torch.inference_mode():
                if self.mode == 'reasoning':
                    prefix_length = inputs['input_ids'].shape[1]
                    sequence = self.model.generate(**inputs, generation_config=config(THINK_TOKENS),
                        stopping_criteria=StoppingCriteriaList([Stop(close[0])]))
                    reasoning = sequence[0, prefix_length:].tolist()
                    generated += len(reasoning)
                    thinking = len(reasoning)
                    reasoning_boundary(reasoning, opening[0], close[0], tokenizer.eos_token_id)
                    # Resume the same assistant turn after its native closing token.
                    spacer = torch.tensor([tokenizer.encode('\n\n', add_special_tokens=False)], device='mps')
                    ids = torch.cat([sequence, spacer], dim=1)
                    inputs = {'input_ids': ids, 'attention_mask': torch.ones_like(ids)}
                prefix_length = inputs['input_ids'].shape[1]
                constraint = FinalConstraint(tokenizer, choices, prefix_length)
                sequence = self.model.generate(**inputs, generation_config=config(FINAL_TOKENS),
                    prefix_allowed_tokens_fn=constraint,
                    stopping_criteria=StoppingCriteriaList([Stop()]))
                final = sequence[0, prefix_length:].tolist()
                generated += len(final)
                if not final or final[-1] != tokenizer.eos_token_id:
                    raise OutputFailure('malformed_output')
                selected = validate_output(tokenizer.decode(final[:-1], skip_special_tokens=False), choices)
        except OutputFailure as exc:
            error = exc.code
        torch.mps.synchronize()
        elapsed = time.perf_counter() - started
        if elapsed >= DECISION_SECONDS:
            raise RuntimeError('90-second decision safety timeout; no partial success')
        return {'status': 'failed' if error else 'passed', 'selected_id': selected,
                'error': error, 'inference_seconds': elapsed, 'generated_tokens': generated,
                'thinking_tokens': thinking}
