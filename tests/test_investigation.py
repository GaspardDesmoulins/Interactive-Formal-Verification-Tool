import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from formal_agent.analysis import analyze
from formal_agent.cli import main
from formal_agent.storage import load


class InvestigationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "example.py"
        self.state = self.root / "state.json"
        self.source.write_text(
            "def withdraw(account, amount):\n"
            "    def nested():\n"
            "        hidden()\n"
            "        raise ValueError()\n"
            "    if amount > 0:\n"
            "        return account.withdraw(amount)\n"
            "    raise ValueError('amount')\n",
            encoding="utf-8",
        )

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = main(list(args))
        return result, stdout.getvalue(), stderr.getvalue()

    def test_analysis_is_syntactic_and_excludes_nested_bodies(self) -> None:
        model = analyze(self.source)
        function = model["functions"][0]
        self.assertEqual(function["parameters"], ["account", "amount"])
        self.assertEqual(function["calls"], ["ValueError", "account.withdraw"])
        self.assertEqual(function["return_lines"], [6])
        self.assertEqual(function["raise_lines"], [7])
        self.assertEqual(len(model["sha256"]), 64)

    def test_start_show_and_resume(self) -> None:
        self.assertEqual(self.run_cli("start", str(self.source), str(self.state))[0], 0)
        result, output, _ = self.run_cli("show", str(self.state))
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output)["unknowns"][0]["status"], "open")
        self.assertEqual(
            self.run_cli("answer", str(self.state), "U1", "--answer", "No overdraft.")[0],
            0,
        )
        state = load(self.state)
        self.assertEqual(state["unknowns"][0]["status"], "answered")
        self.assertEqual(state["unknowns"][0]["answer"], "No overdraft.")
        self.assertEqual(state["unknowns"][0]["affected_obligations"], [])
        self.assertEqual(self.run_cli("answer", str(self.state), "U1", "--answer", "Again")[0], 1)

    def test_invalid_answers_and_existing_state_do_not_overwrite(self) -> None:
        self.assertEqual(self.run_cli("start", str(self.source), str(self.state))[0], 0)
        original = self.state.read_bytes()
        for args in [
            ("start", str(self.source), str(self.state)),
            ("answer", str(self.state), "U1", "--answer", "  "),
            ("answer", str(self.state), "U99", "--answer", "No"),
        ]:
            self.assertEqual(self.run_cli(*args)[0], 1)
            self.assertEqual(self.state.read_bytes(), original)

    def test_syntax_error_and_invalid_state_are_reported(self) -> None:
        self.source.write_text("def broken(:", encoding="utf-8")
        result, _, error = self.run_cli("start", str(self.source), str(self.state))
        self.assertEqual(result, 1)
        self.assertIn("Error:", error)
        self.assertFalse(self.state.exists())
        self.state.write_text('{"schema_version": 9}', encoding="utf-8")
        self.assertEqual(self.run_cli("show", str(self.state))[0], 1)

    def test_target_is_not_executed(self) -> None:
        marker = self.root / "executed"
        self.source.write_text(
            f"open({str(marker)!r}, 'w').write('executed')\n"
            "def f():\n    return 1\n",
            encoding="utf-8",
        )
        self.assertEqual(self.run_cli("start", str(self.source), str(self.state))[0], 0)
        self.assertFalse(marker.exists())

    def test_empty_file_and_non_utf8_source(self) -> None:
        self.source.write_bytes(b"")
        self.assertEqual(analyze(self.source)["functions"], [])
        self.source.write_bytes(
            b"# coding: latin-1\n"
            b"def f():\n"
            b"    'caf\xe9'\n"
            b"    return 1\n"
        )
        self.assertEqual(analyze(self.source)["functions"][0]["docstring"], "caf\u00e9")

    def test_invalid_unknown_is_rejected_without_overwriting(self) -> None:
        self.assertEqual(self.run_cli("start", str(self.source), str(self.state))[0], 0)
        state = load(self.state)
        del state["unknowns"][0]["question"]
        self.state.write_text(json.dumps(state), encoding="utf-8")
        original = self.state.read_bytes()
        result, _, error = self.run_cli("answer", str(self.state), "U1", "--answer", "No")
        self.assertEqual(result, 1)
        self.assertIn("Invalid unknown", error)
        self.assertEqual(self.state.read_bytes(), original)

    def test_corrupted_identity_or_provenance_is_rejected(self) -> None:
        self.assertEqual(self.run_cli("start", str(self.source), str(self.state))[0], 0)
        state = load(self.state)
        corruptions = [
            lambda data: data["unknowns"][0].update(id="U0"),
            lambda data: data["unknowns"][0].update(id="U01"),
            lambda data: data["unknowns"][0].update(id="other"),
            lambda data: data["unknowns"][0].update(id="U2"),
            lambda data: data["unknowns"][0].update(function="missing"),
            lambda data: data["unknowns"][0].update(line=2),
            lambda data: data["unknowns"][0].update(question="  "),
            lambda data: data["unknowns"][0].update(description=""),
            lambda data: data["unknowns"][0].update(impact=" "),
            lambda data: data["unknowns"][0].update(origin=""),
            lambda data: data["model"].update(sha256="wrong"),
            lambda data: data["model"].update(source=""),
            lambda data: data["model"]["functions"][0].update(line=0),
            lambda data: data["model"]["functions"][0].update(end_line=0),
            lambda data: data["model"]["functions"].append(
                copy.deepcopy(data["model"]["functions"][0])
            ),
        ]
        for corrupt in corruptions:
            with self.subTest(corrupt=corrupt):
                broken = copy.deepcopy(state)
                corrupt(broken)
                self.state.write_text(json.dumps(broken), encoding="utf-8")
                original = self.state.read_bytes()
                for args in (
                    ("show", str(self.state)),
                    ("report", str(self.state)),
                    ("answer", str(self.state), "U1", "--answer", "No"),
                ):
                    result, _, error = self.run_cli(*args)
                    self.assertEqual(result, 1)
                    self.assertIn("Error:", error)
                    self.assertEqual(self.state.read_bytes(), original)

    def test_interactive_answer_and_eof(self) -> None:
        self.assertEqual(self.run_cli("start", str(self.source), str(self.state))[0], 0)
        original = self.state.read_bytes()
        with patch("builtins.input", side_effect=EOFError):
            self.assertEqual(self.run_cli("answer", str(self.state), "U1")[0], 1)
        self.assertEqual(self.state.read_bytes(), original)
        with patch("builtins.input", return_value="The balance must stay non-negative.") as prompt:
            self.assertEqual(self.run_cli("answer", str(self.state), "U1")[0], 0)
        self.assertIn("withdraw", prompt.call_args.args[0])
        self.assertEqual(
            load(self.state)["unknowns"][0]["answer"],
            "The balance must stay non-negative.",
        )

    def test_questions_are_separated_by_parameter(self) -> None:
        self.assertEqual(self.run_cli("start", str(self.source), str(self.state))[0], 0)
        unknowns = load(self.state)["unknowns"]
        self.assertEqual([item["id"] for item in unknowns], ["U1", "U2", "U3"])
        self.assertIn("behavior", unknowns[0]["question"])
        self.assertIn("parameter account", unknowns[1]["question"])
        self.assertIn("parameter amount", unknowns[2]["question"])

    def test_imports_are_syntactic_and_scoped(self) -> None:
        self.source.write_text(
            "import os\n"
            "if True:\n"
            "    from pkg import thing as alias\n"
            "def f():\n"
            "    import json\n"
            "    def nested():\n"
            "        import hidden\n"
            "    return json.loads('{}')\n",
            encoding="utf-8",
        )
        model = analyze(self.source)
        self.assertEqual(model["imports"], [
            {"line": 1, "statement": "import os"},
            {"line": 3, "statement": "from pkg import thing as alias"},
        ])
        self.assertEqual(model["functions"][0]["imports"], [
            {"line": 5, "statement": "import json"},
        ])
        self.assertEqual(self.run_cli("start", str(self.source), str(self.state))[0], 0)
        self.assertEqual(load(self.state)["model"]["imports"], model["imports"])

    def test_legacy_snapshot_without_imports_is_still_supported(self) -> None:
        self.assertEqual(self.run_cli("start", str(self.source), str(self.state))[0], 0)
        state = load(self.state)
        del state["model"]["imports"]
        for function in state["model"]["functions"]:
            del function["imports"]
        self.state.write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(self.run_cli("answer", str(self.state), "U1", "--answer", "No")[0], 0)
        self.assertEqual(load(self.state)["unknowns"][0]["answer"], "No")

    def test_changed_or_missing_source_blocks_answers_but_allows_inspection(self) -> None:
        self.assertEqual(self.run_cli("start", str(self.source), str(self.state))[0], 0)
        original = self.state.read_bytes()
        self.source.write_text("def withdraw(): pass\n", encoding="utf-8")
        result, output, error = self.run_cli("show", str(self.state))
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output)["unknowns"][0]["status"], "open")
        self.assertIn("Source has changed", error)
        result, _, error = self.run_cli("answer", str(self.state), "U1", "--answer", "No")
        self.assertEqual(result, 1)
        self.assertIn("Source has changed", error)
        self.assertEqual(self.state.read_bytes(), original)
        self.source.unlink()
        result, _, error = self.run_cli("show", str(self.state))
        self.assertEqual(result, 0)
        self.assertIn("Source cannot be read", error)
        self.assertEqual(self.run_cli("answer", str(self.state), "U1", "--answer", "No")[0], 1)
        self.assertEqual(self.state.read_bytes(), original)

    def test_source_change_during_question_does_not_save_answer(self) -> None:
        self.assertEqual(self.run_cli("start", str(self.source), str(self.state))[0], 0)
        original = self.state.read_bytes()

        def change_source(prompt: str) -> str:
            self.source.write_text("def withdraw(): pass\n", encoding="utf-8")
            return "A response"

        with patch("builtins.input", side_effect=change_source):
            result, _, error = self.run_cli("answer", str(self.state), "U1")
        self.assertEqual(result, 1)
        self.assertIn("Changes were not saved", error)
        self.assertEqual(self.state.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
