"""Local boundary tests only: no ML packages, model downloads or inference."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import local_model as contract
import natta

RUNTIME = Path(__file__).resolve().parents[2] / "natta-local-model"


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


decision = load_module("natta_decision", "decision.py")
probe = load_module("natta_probe", "probe.py")
download = load_module("natta_download", "download.py")


class LocalModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.runtime = contract.runtime_path(self.root)
        self.runtime.mkdir(parents=True)
        (self.runtime / "runtime.toml").write_text((RUNTIME / "runtime.toml").read_text())
        self.model = contract.model_path(self.root)

    def make_model(self):
        self.model.mkdir(parents=True)
        config = {"model_type": "qwen3", "architectures": ["Qwen3ForCausalLM"],
                  "hidden_size": 1024, "num_hidden_layers": 28,
                  "vocab_size": 151936, "num_key_value_heads": 8}
        for name in contract.MODEL_FILES:
            (self.model / name).write_text(json.dumps(config) if name == "config.json" else "fixture")
        self.receipt = {"model_id": contract.MODEL_ID, "revision": contract.REVISION,
                        "source": "https://huggingface.co",
                        "files": {name: download.hashes(self.model / name) for name in contract.MODEL_FILES}}
        self.write_receipt()

    def write_receipt(self):
        (self.model / "natta-model.json").write_text(json.dumps(self.receipt))

    def make_runtime(self):
        python = self.runtime / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.write_text("fixture")
        (self.runtime / "uv.lock").write_text(
            'version = 1\n[[package]]\nname = "openjev"\n'
            f'source = {{ git = "{contract.REPOSITORY}?rev={contract.COMMIT}#{contract.COMMIT}" }}\n')
        return {"python": [3, 12, 15], "repository": contract.REPOSITORY,
                "commit": contract.COMMIT, "versions": {}}

    def test_paths_and_exact_identity(self):
        self.assertEqual(self.runtime, self.root / "tools/natta-local-model")
        self.assertEqual(self.model, self.root / "Library/Application Support/NattaToolkit/models/Qwen3-0.6B" / contract.REVISION)
        self.assertTrue(self.model.is_absolute())
        self.assertEqual(contract.MODEL_ID, "Qwen/Qwen3-0.6B")
        self.assertEqual(contract.REVISION, "c1899de289a04d12100db370d81485cdf75e47ca")
        self.assertEqual(contract.COMMIT, "67eedd02d8863dfe37fcd391c55ce2a7b6d82e37")

    def test_dependency_declaration_pins_git_and_qwen_compatible_transformers(self):
        project = tomllib.loads((RUNTIME / "pyproject.toml").read_text())
        self.assertEqual(project["tool"]["uv"]["sources"]["openjev"],
                         {"git": contract.REPOSITORY, "rev": contract.COMMIT})
        self.assertIn("transformers>=4.51.0", project["project"]["dependencies"])

    def test_missing_environment_and_model_are_explicit(self):
        with patch.object(contract.subprocess, "run", side_effect=AssertionError("launched runtime")):
            checks = contract.doctor_checks(self.runtime, self.model)
        self.assertFalse(any(passed for passed, _ in checks))
        self.assertIn("Missing isolated environment", checks[0][1])
        self.assertIn("Missing local model artifact", checks[1][1])

    def test_model_identity_config_and_receipt(self):
        self.make_model()
        contract.validate_model(self.model)
        for key, value in (("revision", "main"), ("model_id", "Other/model"),
                           ("source", "https://hf-mirror.com")):
            with self.subTest(key=key):
                before = self.receipt[key]
                self.receipt[key] = value
                self.write_receipt()
                with self.assertRaisesRegex(ValueError, "identity"):
                    contract.validate_model(self.model)
                self.receipt[key] = before
        self.write_receipt()
        config = self.model / "config.json"
        data = json.loads(config.read_text())
        data["hidden_size"] = 2048
        config.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "config mismatch"):
            contract.validate_model(self.model)

    def test_full_artifact_verification_detects_same_size_corruption(self):
        self.make_model()
        download.verify_local(self.model)
        (self.model / "model.safetensors").write_text("changed")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            download.verify_local(self.model)

    def test_doctor_present_is_read_only_and_offline_without_model_import(self):
        self.make_model()
        metadata = self.make_runtime()
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        def inspect_only(argv, **kwargs):
            self.assertEqual(argv[1:3], ["-I", "-B"])
            self.assertEqual(Path(argv[-1]).name, "probe.py")
            self.assertEqual(kwargs["env"]["HF_HUB_OFFLINE"], "1")
            self.assertEqual(kwargs["env"]["HF_HUB_DISABLE_TELEMETRY"], "1")
            return subprocess.CompletedProcess(argv, 0, json.dumps(metadata), "")
        with patch.object(contract.subprocess, "run", side_effect=inspect_only), \
             patch.dict(sys.modules, {"torch": None, "transformers": None, "openjev": None}):
            self.assertTrue(all(passed for passed, _ in contract.doctor_checks(self.runtime, self.model)))
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_probe_uses_metadata_only(self):
        for filename in ("openjev/core.py", "openjev/types.py", "openjev/__init__.py"):
            path = self.root / filename
            path.parent.mkdir(exist_ok=True)
            path.write_text("raise AssertionError('must not be imported')")
        distribution = Mock()
        distribution.read_text.return_value = json.dumps({"url": contract.REPOSITORY,
            "vcs_info": {"commit_id": contract.COMMIT}})
        distribution.locate_file.side_effect = lambda filename: self.root / filename
        with patch.object(probe.sys, "version_info", (3, 12, 15)), \
             patch.object(probe.sys, "prefix", str(RUNTIME / ".venv")), \
             patch.object(probe.sys, "base_prefix", "/fixture/managed-python"), \
             patch.object(probe.importlib.metadata, "distribution", return_value=distribution), \
             patch.object(probe.importlib.metadata, "version", return_value="fixture"), \
             patch.dict(sys.modules, {"openjev": None, "torch": None, "transformers": None}):
            self.assertEqual(probe.inspect()["commit"], contract.COMMIT)
            distribution.read_text.return_value = '{}'
            with self.assertRaisesRegex(ValueError, "approved Git source"):
                probe.inspect()

    def test_probe_rejects_nonisolated_python(self):
        with patch.object(probe.sys, "version_info", (3, 12, 15)), \
             patch.object(probe.sys, "prefix", "/fixture/global-python"), \
             patch.object(probe.sys, "base_prefix", "/fixture/global-python"), \
             patch.object(probe.importlib.metadata, "distribution", side_effect=AssertionError("read global package")):
            with self.assertRaisesRegex(ValueError, "own .venv"):
                probe.inspect()

    def test_lock_and_installed_source_mismatch_fail(self):
        metadata = self.make_runtime()
        with patch.object(contract.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps({**metadata, "commit": "wrong"}), "")):
            with self.assertRaisesRegex(ValueError, "identity mismatch"):
                contract.inspect_runtime(self.runtime)
        lock = self.runtime / "uv.lock"
        lock.write_text(lock.read_text().replace(contract.COMMIT, "main"))
        with patch.object(contract.subprocess, "run", side_effect=AssertionError("launched probe")):
            with self.assertRaisesRegex(ValueError, "approved commit"):
                contract.inspect_runtime(self.runtime)

    def test_no_cloud_configuration(self):
        contract.validate_config(self.runtime)
        config = self.runtime / "runtime.toml"
        config.write_text(config.read_text().replace('backend = "pytorch-mps"', 'backend = "remote"'))
        with self.assertRaisesRegex(ValueError, "approved manifest"):
            contract.validate_config(self.runtime)
        self.assertFalse(contract.CONFIG["cloud_fallback"])

    def test_invalid_choice_sets_rejected_before_model_loading(self):
        engine = decision.LocalDecision()
        for choices in ({}, {"a": "one"}, {str(i): "description" for i in range(27)},
                        {"a": "", "b": "valid"}, {"": "meaning", "b": "valid"}, ["a", "b"]):
            with self.subTest(choices=choices), patch.object(engine, "_load_engine", side_effect=AssertionError("loaded model")):
                with self.assertRaises(ValueError):
                    engine.decide("state", "instructions", choices)
        decision.validate_request("state", "instructions", {str(i): "meaning" for i in range(26)})

    def test_translation_preserves_ids_and_score_semantics(self):
        choices = {"project.build": "compilation", "user.other": "other"}
        result = decision.translate({"type": "choice", "choice": "project.build",
            "probabilities": {"project.build": 0.75, "user.other": 0.25}, "confidence": 0.5}, choices, self.model, 1.2, 0.3)
        self.assertEqual(result["selected_id"], "project.build")
        self.assertEqual(set(result["probabilities"]), set(choices))
        self.assertEqual(result["concentration"], 0.5)
        self.assertEqual(result["model"]["revision"], contract.REVISION)
        self.assertNotIn("correctness_probability", result)
        self.assertEqual((result["backend"], result["dtype"]), ("pytorch-mps", "float32"))

    def test_malformed_output_fails(self):
        choices = {"a": "one", "b": "two"}
        valid = {"type": "choice", "choice": "a", "probabilities": {"a": 0.75, "b": 0.25}, "confidence": 0.5}
        for changes in ({"type": "score"}, {"choice": "invented"}, {"choice": "b"},
                        {"probabilities": {"a": float("nan"), "b": 0.25}},
                        {"probabilities": {"a": True, "b": 0.25}},
                        {"probabilities": {"a": 0.8, "b": 0.3}},
                        {"probabilities": {"a": 1.0}}, {"confidence": 0.97}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                decision.translate({**valid, **changes}, choices, self.model, 0, 0)

    def test_mocked_decision_never_executes_selected_id_and_reuses_engine(self):
        types_module, core_module = ModuleType("openjev.types"), ModuleType("openjev.core")
        types_module.Choice = lambda **kwargs: SimpleNamespace(**kwargs)
        core_module._build_prompt = lambda *args: "prompt"
        tokenizer = SimpleNamespace(encode=lambda text, **kwargs: list(text))
        fake = SimpleNamespace(tokenizer=tokenizer, system_prompt="system",
            torch=SimpleNamespace(mps=SimpleNamespace(synchronize=lambda: None)),
            answer_one=Mock(return_value={"type": "choice", "choice": "never-execute",
                "probabilities": {"never-execute": 0.8, "other": 0.2}, "confidence": 0.6}))
        engine = decision.LocalDecision()
        with patch.object(engine, "_load_engine", return_value=fake) as load, \
             patch.dict(sys.modules, {"openjev.types": types_module, "openjev.core": core_module}), \
             patch("subprocess.run", side_effect=AssertionError("executed action")):
            for _ in range(2):
                answer = engine.decide("state", "instructions", {"never-execute": "one", "other": "two"})
                self.assertEqual(answer["selected_id"], "never-execute")
            load.assert_called_once()
            self.assertEqual(answer["load_seconds"], 0)

    def test_doctor_optional_omissions_do_not_break_core_health(self):
        out = io.StringIO()
        with patch.object(natta.routing, "doctor_checks", return_value=[(True, "Semantic router fixture ready")]), \
             patch.object(natta.local_model, "doctor_checks", return_value=[(False, "OpenJEV runtime missing")]), \
             patch.object(natta.shutil, "which", side_effect=lambda name: str(natta.WORKSPACE_ROOT / "bin/natta") if name == "natta" else "/fixture/bin/tool"), \
             contextlib.redirect_stdout(out):
            self.assertEqual(natta.doctor([]), 0)
        self.assertIn("UNAVAILABLE  OpenJEV runtime missing", out.getvalue())


if __name__ == "__main__":
    unittest.main()
