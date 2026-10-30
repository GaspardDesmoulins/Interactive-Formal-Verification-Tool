import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from formal_agent.cli import main
from formal_agent.storage import load


class PropertyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "example.py"
        self.state = self.root / "state.json"
        self.source.write_text("def f(x):\n    return x\n", encoding="utf-8")
        self.assertEqual(self.run_cli("start", str(self.source), str(self.state))[0], 0)

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def answer(self) -> None:
        self.assertEqual(
            self.run_cli("answer", str(self.state), "U2", "--answer", "Positive integers")[0],
            0,
        )

    def test_candidate_acceptance_and_provenance(self) -> None:
        self.answer()
        code, output, _ = self.run_cli(
            "add-property", str(self.state), "U2", "--statement", "x is positive"
        )
        self.assertEqual(code, 0)
        self.assertIn("P1", output)
        property_item = load(self.state)["properties"][0]
        self.assertEqual(property_item, {
            "id": "P1",
            "statement": "x is positive",
            "status": "candidate",
            "origin": "user",
            "source_unknown_id": "U2",
            "accepted_by": None,
        })
        code, output, _ = self.run_cli("accept-property", str(self.state), "P1")
        self.assertEqual(code, 0)
        self.assertIn("not proved", output)
        property_item = load(self.state)["properties"][0]
        self.assertEqual(property_item["status"], "accepted")
        self.assertEqual(property_item["accepted_by"], "user")
        self.assertEqual(property_item["source_unknown_id"], "U2")

    def test_invalid_transitions_leave_state_unchanged(self) -> None:
        original = self.state.read_bytes()
        invalid = [
            ("add-property", str(self.state), "U2", "--statement", "x is positive"),
            ("add-property", str(self.state), "U99", "--statement", "x is positive"),
            ("accept-property", str(self.state), "P1"),
        ]
        for args in invalid:
            self.assertEqual(self.run_cli(*args)[0], 1)
            self.assertEqual(self.state.read_bytes(), original)
        self.answer()
        original = self.state.read_bytes()
        self.assertEqual(
            self.run_cli("add-property", str(self.state), "U2", "--statement", "  ")[0],
            1,
        )
        self.assertEqual(self.state.read_bytes(), original)

    def test_changed_source_blocks_property_updates(self) -> None:
        self.answer()
        self.assertEqual(
            self.run_cli("add-property", str(self.state), "U2", "--statement", "x > 0")[0],
            0,
        )
        original = self.state.read_bytes()
        self.source.write_text("def f(x):\n    return x + 1\n", encoding="utf-8")
        self.assertEqual(
            self.run_cli("add-property", str(self.state), "U2", "--statement", "x < 10")[0],
            1,
        )
        self.assertEqual(self.run_cli("accept-property", str(self.state), "P1")[0], 1)
        self.assertEqual(self.state.read_bytes(), original)

    def test_schema_one_migrates_on_write_not_on_read(self) -> None:
        state = load(self.state)
        state["schema_version"] = 1
        del state["properties"]
        del state["links"]
        del state["assumptions"]
        del state["workflow"]
        del state["conflicts"]
        del state["scenarios"]
        del state["scenario_suggestions"]
        del state["test_call_analyses"]
        self.state.write_text(json.dumps(state), encoding="utf-8")
        original = self.state.read_bytes()
        result, output, _ = self.run_cli("show", str(self.state))
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output)["schema_version"], 10)
        self.assertEqual(self.state.read_bytes(), original)
        self.answer()
        self.assertEqual(json.loads(self.state.read_text(encoding="utf-8"))["schema_version"], 10)

    def test_invalid_property_provenance_is_rejected(self) -> None:
        self.answer()
        self.assertEqual(
            self.run_cli("add-property", str(self.state), "U2", "--statement", "x > 0")[0],
            0,
        )
        state = load(self.state)
        state["properties"][0]["source_unknown_id"] = "U1"
        self.state.write_text(json.dumps(state), encoding="utf-8")
        original = self.state.read_bytes()
        code, _, error = self.run_cli("show", str(self.state))
        self.assertEqual(code, 1)
        self.assertIn("Invalid property or provenance", error)
        self.assertEqual(self.state.read_bytes(), original)

    def test_invalid_acceptance_metadata_is_rejected(self) -> None:
        self.answer()
        self.assertEqual(
            self.run_cli("add-property", str(self.state), "U2", "--statement", "x > 0")[0],
            0,
        )
        state = load(self.state)
        state["properties"][0]["status"] = "accepted"
        self.state.write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(self.run_cli("show", str(self.state))[0], 1)


if __name__ == "__main__":
    unittest.main()
