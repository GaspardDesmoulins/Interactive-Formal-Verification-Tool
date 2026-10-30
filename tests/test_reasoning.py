import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from formal_agent.cli import main
from formal_agent.reasoning import apply_proposals
from formal_agent.storage import load


class ReasoningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "example.py"
        self.state = self.root / "state.json"
        self.proposals = self.root / "proposals.json"
        self.source.write_text("def f(x):\n    return x\n", encoding="utf-8")
        self.assertEqual(self.run_cli("start", str(self.source), str(self.state))[0], 0)
        self.data = {
            "source_sha256": load(self.state)["model"]["sha256"],
            "properties": [{"function": "f", "line": 1, "statement": "x is non-negative"}],
            "unknowns": [{
                "function": "f",
                "line": 1,
                "description": "Negative input behavior is unspecified.",
                "question": "Can x be negative?",
                "impact": "The input domain is unclear.",
            }],
        }

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def import_data(self, data: object) -> tuple[int, str, str]:
        self.proposals.write_text(json.dumps(data), encoding="utf-8")
        return self.run_cli(
            "import-proposals", str(self.state), str(self.proposals), "--reasoner", "test-model"
        )

    def test_import_persists_unverified_proposals_and_user_acceptance(self) -> None:
        result, output, _ = self.import_data(self.data)
        self.assertEqual(result, 0)
        self.assertIn("1 candidate properties and 1 unknowns", output)
        state = load(self.state)
        property_item = state["properties"][0]
        self.assertEqual(property_item["status"], "candidate")
        self.assertEqual(property_item["origin"], "llm")
        self.assertEqual(property_item["source_function"], {"name": "f", "line": 1})
        self.assertEqual(property_item["reasoner"], "test-model")
        self.assertEqual(state["unknowns"][-1]["status"], "open")
        self.assertEqual(state["unknowns"][-1]["evidence"], [])
        self.assertEqual(state["unknowns"][-1]["origin"], "LLM proposal (test-model)")
        self.assertEqual(self.run_cli("accept-property", str(self.state), "P1")[0], 0)
        report = self.run_cli("report", str(self.state))[1]
        self.assertIn('Origin: LLM proposal; reasoner: "test-model"', report)
        self.assertIn('Origin: "LLM proposal (test-model)"', report)
        self.assertIn("Accepted by: user; verification: not performed", report)

    def test_invalid_proposals_are_atomic(self) -> None:
        original = self.state.read_bytes()
        for data in [
            {"properties": [], "unknowns": []},
            {**self.data, "source_sha256": "wrong"},
            {**self.data, "properties": [], "unknowns": []},
            {**self.data, "properties": [{"function": "f", "line": True, "statement": "x"}]},
            {**self.data, "unknowns": [{"function": "f", "line": 1}]},
            {**self.data, "unknowns": [{**self.data["unknowns"][0], "line": 50}]},
            {**self.data, "properties": self.data["properties"] * 2},
        ]:
            self.assertEqual(self.import_data(data)[0], 1)
            self.assertEqual(self.state.read_bytes(), original)

    def test_duplicate_import_does_not_change_state(self) -> None:
        self.assertEqual(self.import_data(self.data)[0], 0)
        original = self.state.read_bytes()
        code, _, error = self.import_data(self.data)
        self.assertEqual(code, 1)
        self.assertIn("Duplicate", error)
        self.assertEqual(self.state.read_bytes(), original)

    def test_answered_question_is_not_imported_again(self) -> None:
        self.assertEqual(
            self.run_cli("answer", str(self.state), "U2", "--answer", "nonnegative")[0], 0
        )
        original = self.state.read_bytes()
        unknown = load(self.state)["unknowns"][1]
        proposal = {
            **self.data,
            "properties": [],
            "unknowns": [{
                "function": "f", "line": 1,
                "description": "Input constraints.",
                "question": "  " + unknown["question"].upper() + "  ",
                "impact": "Input domain.",
            }],
        }
        code, _, error = self.import_data(proposal)
        self.assertEqual(code, 1)
        self.assertIn("Duplicate", error)
        self.assertEqual(self.state.read_bytes(), original)

    def test_invalid_json_does_not_change_state(self) -> None:
        original = self.state.read_bytes()
        self.proposals.write_text("{", encoding="utf-8")
        code, _, error = self.run_cli(
            "import-proposals", str(self.state), str(self.proposals), "--reasoner", "test-model"
        )
        self.assertEqual(code, 1)
        self.assertIn("Error:", error)
        self.assertEqual(self.state.read_bytes(), original)

    def test_changed_source_blocks_import(self) -> None:
        original = self.state.read_bytes()
        self.source.write_text("def f(x):\n    return 0\n", encoding="utf-8")
        self.assertEqual(self.import_data(self.data)[0], 1)
        self.assertEqual(self.state.read_bytes(), original)

    def test_provider_cannot_mutate_the_investigation_model(self) -> None:
        state = load(self.state)
        original_model = copy.deepcopy(state["model"])
        data = self.data

        class FakeProvider:
            def propose(self, model: dict) -> object:
                model["functions"].clear()
                return data

        self.assertEqual(apply_proposals(state, FakeProvider(), "fake"), (1, 1))
        self.assertEqual(state["model"], original_model)

    def test_version_two_snapshot_is_read_and_upgraded_on_write(self) -> None:
        state = load(self.state)
        state["schema_version"] = 2
        del state["links"]
        del state["assumptions"]
        del state["workflow"]
        self.state.write_text(json.dumps(state), encoding="utf-8")
        original = self.state.read_bytes()
        self.assertEqual(self.run_cli("show", str(self.state))[0], 0)
        self.assertEqual(self.state.read_bytes(), original)
        self.assertEqual(self.import_data(self.data)[0], 0)
        self.assertEqual(json.loads(self.state.read_text(encoding="utf-8"))["schema_version"], 6)


if __name__ == "__main__":
    unittest.main()
