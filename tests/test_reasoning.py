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
            {**self.data, "unknowns": self.data["unknowns"] * 2 + [{"function": "f"}]},
        ]:
            self.assertEqual(self.import_data(data)[0], 1)
            self.assertEqual(self.state.read_bytes(), original)

    def test_only_duplicate_import_reports_matches_without_changing_state(self) -> None:
        self.assertEqual(self.import_data(self.data)[0], 0)
        original = self.state.read_bytes()
        code, output, error = self.import_data(self.data)
        self.assertEqual(code, 0)
        self.assertEqual(error, "")
        self.assertIn("Skipped exact property proposal #1; matches P1.", output)
        self.assertIn("Skipped exact unknown proposal #1; matches U3.", output)
        self.assertIn("No new proposals; investigation unchanged.", output)
        self.assertEqual(self.state.read_bytes(), original)

    def test_mixed_existing_and_internal_duplicates_keep_new_proposals(self) -> None:
        self.assertEqual(self.import_data(self.data)[0], 0)
        new_property = {**self.data["properties"][0], "statement": "x is at most 100"}
        new_unknown = {
            **self.data["unknowns"][0],
            "question": "Can x exceed 100?",
            "description": "The upper input limit is unspecified.",
        }
        proposal = {
            **self.data,
            "properties": [self.data["properties"][0], new_property, new_property],
            "unknowns": [self.data["unknowns"][0], new_unknown, new_unknown],
        }
        code, output, error = self.import_data(proposal)
        self.assertEqual((code, error), (0, ""))
        self.assertIn("1 candidate properties and 1 unknowns", output)
        for expected in (
            "property proposal #1; matches P1",
            "property proposal #3; matches P2",
            "unknown proposal #1; matches U3",
            "unknown proposal #3; matches U4",
        ):
            self.assertIn(expected, output)
        state = load(self.state)
        self.assertEqual([item["id"] for item in state["properties"]], ["P1", "P2"])
        self.assertEqual(state["unknowns"][-1]["question"], new_unknown["question"])
        self.assertEqual(len(state["unknowns"]), 4)

    def test_invalid_item_after_duplicates_and_new_items_is_atomic(self) -> None:
        self.assertEqual(self.import_data(self.data)[0], 0)
        original = self.state.read_bytes()
        proposal = {
            **self.data,
            "properties": [
                self.data["properties"][0],
                {**self.data["properties"][0], "statement": "a new property"},
            ],
            "unknowns": [self.data["unknowns"][0], {"function": "f"}],
        }
        code, _, error = self.import_data(proposal)
        self.assertEqual(code, 1)
        self.assertIn("Invalid proposal fields", error)
        self.assertEqual(self.state.read_bytes(), original)

    def test_same_question_with_different_details_is_kept_for_review(self) -> None:
        self.assertEqual(
            self.run_cli("answer", str(self.state), "U2", "--answer", "nonnegative")[0], 0
        )
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
        code, output, error = self.import_data(proposal)
        self.assertEqual((code, error), (0, ""))
        self.assertIn("Unknown U3 has the same normalized question as U2", output)
        state = load(self.state)
        self.assertEqual(state["unknowns"][1]["status"], "answered")
        self.assertEqual(state["unknowns"][2]["description"], "Input constraints.")
        self.assertEqual(state["unknowns"][2]["impact"], "Input domain.")
        self.assertEqual(state["unknowns"][2]["status"], "open")
        self.assertEqual(
            self.run_cli("mark-duplicate", str(self.state), "U3", "U2", "--reason", "reviewed")[0],
            0,
        )

    def test_same_question_with_different_impact_inside_batch_is_kept(self) -> None:
        first = self.data["unknowns"][0]
        second = {**first, "impact": "Another consequence."}
        code, output, error = self.import_data({**self.data, "unknowns": [first, second]})
        self.assertEqual((code, error), (0, ""))
        self.assertIn("Unknown U4 has the same normalized question as U3", output)
        self.assertEqual(load(self.state)["unknowns"][-1]["impact"], "Another consequence.")

    def test_exact_answered_question_is_not_imported_again(self) -> None:
        self.assertEqual(self.import_data(self.data)[0], 0)
        self.assertEqual(self.run_cli("answer", str(self.state), "U3", "--answer", "no")[0], 0)
        original = self.state.read_bytes()
        code, output, _ = self.import_data({**self.data, "properties": []})
        self.assertEqual(code, 0)
        self.assertIn("Skipped exact unknown proposal #1; matches U3.", output)
        self.assertEqual(self.state.read_bytes(), original)

    def test_property_from_different_reasoner_is_preserved(self) -> None:
        self.assertEqual(self.import_data(self.data)[0], 0)
        self.proposals.write_text(json.dumps({**self.data, "unknowns": []}), encoding="utf-8")
        code, _, _ = self.run_cli(
            "import-proposals", str(self.state), str(self.proposals), "--reasoner", "other-model"
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            [item["reasoner"] for item in load(self.state)["properties"]],
            ["test-model", "other-model"],
        )

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

        result = apply_proposals(state, FakeProvider(), "fake")
        self.assertEqual((result.properties_added, result.unknowns_added), (1, 1))
        self.assertEqual(state["model"], original_model)

    def test_version_two_snapshot_is_read_and_upgraded_on_write(self) -> None:
        state = load(self.state)
        state["schema_version"] = 2
        del state["links"]
        del state["assumptions"]
        del state["workflow"]
        del state["conflicts"]
        self.state.write_text(json.dumps(state), encoding="utf-8")
        original = self.state.read_bytes()
        self.assertEqual(self.run_cli("show", str(self.state))[0], 0)
        self.assertEqual(self.state.read_bytes(), original)
        self.assertEqual(self.import_data(self.data)[0], 0)
        self.assertEqual(json.loads(self.state.read_text(encoding="utf-8"))["schema_version"], 7)


if __name__ == "__main__":
    unittest.main()
