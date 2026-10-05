"""Explicit local bounded decision. A returned choice is data, never an action."""
import argparse
import json
import math
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "natta"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import local_model as contract


def validate_request(state, instructions, choices):
    if not isinstance(state, str) or not state.strip():
        raise ValueError("state must be nonempty text")
    if not isinstance(instructions, str) or not instructions.strip():
        raise ValueError("instructions must be nonempty text")
    if not isinstance(choices, dict) or not 2 <= len(choices) <= 26:
        raise ValueError("choices must contain 2–26 caller IDs and descriptions")
    if any(not isinstance(key, str) or not key.strip() or
           not isinstance(value, str) or not value.strip() for key, value in choices.items()):
        raise ValueError("choice IDs and semantic descriptions must be nonempty strings")


def translate(answer, choices, model, load_seconds, inference_seconds, profile=contract.DEFAULT_PROFILE):
    """Reject malformed output; probabilities/concentration are not correctness."""
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        raise ValueError("Malformed OpenJEV choice output")
    probabilities = answer.get("probabilities")
    if not isinstance(probabilities, dict) or set(probabilities) != set(choices):
        raise ValueError("OpenJEV probability IDs differ from caller choices")
    def probability(value):
        return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1
    if not all(probability(value) for value in probabilities.values()):
        raise ValueError("OpenJEV returned invalid candidate probabilities")
    if abs(sum(probabilities.values()) - 1) > len(choices) * 0.00005 + 1e-9:
        raise ValueError("OpenJEV candidate probabilities are not normalized")
    selected = answer.get("choice")
    if not isinstance(selected, str) or selected not in choices or probabilities[selected] != max(probabilities.values()):
        raise ValueError("OpenJEV selected an invalid or nonmaximal caller ID")
    concentration = answer.get("confidence")
    expected = (len(choices) * max(probabilities.values()) - 1) / (len(choices) - 1)
    if not probability(concentration) or abs(concentration - expected) > 0.0002:
        raise ValueError("OpenJEV returned invalid distribution concentration")
    approved = contract.model_profile(profile)
    return {
        "status": "passed", "selected_id": selected,
        "probabilities": probabilities, "concentration": concentration,
        "probability_semantics": "candidate-normalized token scores; uncalibrated",
        "model": {"id": approved.model_id, "revision": approved.revision, "path": str(model)},
        "openjev_commit": contract.COMMIT, "backend": "pytorch-mps",
        "device": "mps", "dtype": "float32",
        "load_seconds": load_seconds, "inference_seconds": inference_seconds,
    }


class LocalDecision:
    """Own one explicit LocalJev instance; no factories/backends/fallbacks."""
    def __init__(self, profile=contract.DEFAULT_PROFILE):
        contract.model_profile(profile)
        self.profile = profile
        self.model_path = contract.model_path(profile=profile)
        self.engine = None

    def _load_engine(self):
        import os
        os.environ.update({key: value for key, value in contract.offline_environment().items()
                           if key.startswith(("HF_", "TRANSFORMERS_", "PYTHONDONTWRITE"))})
        contract.validate_config(contract.runtime_path())
        contract.validate_model(self.model_path, self.profile)
        # Check source metadata without executing OpenJEV's package initializer.
        from probe import inspect
        inspect()
        import torch
        if not torch.backends.mps.is_available():
            raise RuntimeError("PyTorch MPS unavailable; no CPU or hosted fallback")
        from openjev.core import LocalJev
        engine = LocalJev(str(self.model_path), device="mps", dtype="float32")
        parameter = next(engine.model.parameters())
        if engine.device != "mps" or parameter.device.type != "mps" or parameter.dtype != torch.float32:
            raise RuntimeError("LocalJev did not load the required MPS/FP32 model")
        torch.mps.synchronize()
        return engine

    def decide(self, state, instructions, choices):
        validate_request(state, instructions, choices)
        started = time.perf_counter()
        if self.engine is None:
            self.engine = self._load_engine()
            load_seconds = time.perf_counter() - started
        else:
            load_seconds = 0.0
        from openjev.types import Choice
        # Enforce a small input bound before the expensive forward pass. This is
        # a boundary resource limit, not a production routing policy.
        from openjev.core import _build_prompt
        question = Choice(instructions=instructions, criteria=choices)
        prompt = _build_prompt(self.engine.tokenizer, state, question, self.engine.system_prompt)
        if len(self.engine.tokenizer.encode(prompt, add_special_tokens=False)) > 4096:
            raise ValueError("Decision prompt exceeds 4096 tokens; no truncation")
        # The upstream verifies standalone labels; verify the actual answer boundary.
        prefix = self.engine.tokenizer.encode(prompt, add_special_tokens=False)
        for label in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[:len(choices)]:
            token = self.engine.tokenizer.encode(label, add_special_tokens=False)
            if self.engine.tokenizer.encode(prompt + label, add_special_tokens=False) != prefix + token:
                raise ValueError("Answer label tokenization changes at prompt boundary")
        self.engine.torch.mps.synchronize()
        started = time.perf_counter()
        answer = self.engine.answer_one(question, state)
        self.engine.torch.mps.synchronize()
        elapsed = time.perf_counter() - started
        return translate(answer, choices, self.model_path, load_seconds, elapsed, self.profile)


SMOKE = {
    "state": "The iOS project fails during simulator compilation.",
    "instructions": "Which topic best describes this issue?",
    "choices": {
        "build": "Compiling the iOS project for the simulator.",
        "test": "Executing tests after successful compilation.",
        "unrelated": "A topic unrelated to compilation or tests.",
    },
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=tuple(contract.PROFILES), default=contract.DEFAULT_PROFILE)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--smoke", action="store_true")
    group.add_argument("--request", type=Path, help="JSON object with state, instructions, choices")
    args = parser.parse_args()
    try:
        request = SMOKE if args.smoke else json.loads(args.request.read_text())
        if not isinstance(request, dict) or set(request) != {"state", "instructions", "choices"}:
            raise ValueError("Request requires state, instructions and choices only")
        print(json.dumps(LocalDecision(args.model).decide(**request), allow_nan=False))
        return 0
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
