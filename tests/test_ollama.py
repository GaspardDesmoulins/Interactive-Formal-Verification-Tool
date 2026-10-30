import contextlib
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from formal_agent.analysis import analyze
from formal_agent.cli import main
from formal_agent.ollama import ENDPOINT, MAX_INPUT_BYTES, MODEL, NoRedirect, OllamaProvider
from formal_agent.storage import load


class Response:
    def __init__(self, value: object) -> None:
        self.raw = json.dumps(value).encode("utf-8")

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def read(self, size: int) -> bytes:
        return self.raw[:size]


class OllamaTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / "example.py"
        self.state = self.root / "state.json"
        self.source.write_text(
            "import math\n"
            "def f(x):\n"
            "    return math.sqrt(x)\n",
            encoding="utf-8",
        )
        self.assertEqual(self.run_cli("start", str(self.source), str(self.state))[0], 0)
        self.proposal = {
            "properties": [{"function": "f", "line": 2, "statement": "x must be non-negative"}],
            "unknowns": [{
                "function": "f", "line": 2,
                "description": "Negative input behavior is unclear.",
                "question": "Should negative inputs be rejected?",
                "impact": "The input domain is unspecified.",
            }],
        }

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def response(self, **overrides: object) -> Response:
        payload = {
            "done": True,
            "done_reason": "stop",
            "message": {"content": json.dumps(self.proposal)},
        }
        payload.update(overrides)
        return Response(payload)

    def test_cli_generates_and_persists_unverified_proposals(self) -> None:
        with patch("formal_agent.ollama.urllib.request.build_opener") as build_opener:
            build_opener.return_value.open.return_value = self.response()
            result, output, error = self.run_cli("propose-ollama", str(self.state))
        self.assertEqual(result, 0)
        self.assertEqual(error, "")
        self.assertIn("1 candidate properties and 1 unknowns", output)
        request = build_opener.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, ENDPOINT)
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], MODEL)
        self.assertFalse(payload["think"])
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["options"]["num_predict"], 1400)
        context = json.loads(payload["messages"][1]["content"])
        self.assertEqual(context["functions"][0]["source"], "def f(x):\n    return math.sqrt(x)")
        self.assertNotIn(str(self.source), payload["messages"][1]["content"])
        state = load(self.state)
        self.assertEqual(state["properties"][0]["status"], "candidate")
        self.assertEqual(state["properties"][0]["reasoner"], "ollama:bonsai-2:4b")
        self.assertEqual(state["unknowns"][-1]["status"], "open")
        self.assertIn("verification: not performed", self.run_cli("report", str(self.state))[1])

    def test_incomplete_or_invalid_outputs_do_not_change_investigation(self) -> None:
        original = self.state.read_bytes()
        responses = [
            self.response(done=False),
            self.response(done_reason="length"),
            self.response(message={"content": "not json"}),
            self.response(message={"content": '{"properties": [], "unknowns": []}'}),
            self.response(message={"content": '{"properties": [], "unknowns": [], "other": 1}'}),
            self.response(message={"content": json.dumps({
                "properties": [{"function": "f", "line": 999, "statement": "x"}],
                "unknowns": [],
            })}),
        ]
        for response in responses:
            with self.subTest(response=response.raw):
                with patch("formal_agent.ollama.urllib.request.build_opener") as builder:
                    builder.return_value.open.return_value = response
                    result, _, error = self.run_cli("propose-ollama", str(self.state))
                self.assertEqual(result, 1)
                self.assertIn("Error:", error)
                self.assertEqual(self.state.read_bytes(), original)

    def test_network_failure_and_invalid_budgets_are_visible(self) -> None:
        original = self.state.read_bytes()
        with patch("formal_agent.ollama.urllib.request.build_opener") as builder:
            builder.return_value.open.side_effect = urllib.error.URLError("connection refused")
            result, _, error = self.run_cli("propose-ollama", str(self.state))
        self.assertEqual(result, 1)
        self.assertIn("Cannot reach local Ollama", error)
        for option in ("--timeout", "--max-tokens"):
            result, _, error = self.run_cli("propose-ollama", str(self.state), option, "0")
            self.assertEqual(result, 1)
            self.assertIn("must be positive", error)
        self.assertEqual(self.state.read_bytes(), original)

    def test_redirect_is_rejected(self) -> None:
        request = urllib.request.Request(ENDPOINT)
        with self.assertRaises(urllib.error.HTTPError):
            NoRedirect().redirect_request(request, None, 302, "Found", {}, "http://example.com")

    def test_source_change_during_inference_prevents_write(self) -> None:
        original = self.state.read_bytes()

        def change_source(*args: object, **kwargs: object) -> Response:
            self.source.write_text("def f(x): return x\n", encoding="utf-8")
            return self.response()

        with patch("formal_agent.ollama.urllib.request.build_opener") as builder:
            builder.return_value.open.side_effect = change_source
            result, _, error = self.run_cli("propose-ollama", str(self.state))
        self.assertEqual(result, 1)
        self.assertIn("Changes were not saved", error)
        self.assertEqual(self.state.read_bytes(), original)

    def test_investigation_change_during_inference_prevents_overwrite(self) -> None:
        def change_investigation(*args: object, **kwargs: object) -> Response:
            result, _, _ = self.run_cli(
                "answer", str(self.state), "U1", "--answer", "An independent answer"
            )
            self.assertEqual(result, 0)
            return self.response()

        with patch("formal_agent.ollama.urllib.request.build_opener") as builder:
            builder.return_value.open.side_effect = change_investigation
            result, _, error = self.run_cli("propose-ollama", str(self.state))
        self.assertEqual(result, 1)
        self.assertIn("Investigation changed", error)
        state = load(self.state)
        self.assertEqual(state["unknowns"][0]["answer"], "An independent answer")
        self.assertEqual(state["properties"], [])

    def test_source_hash_and_input_limit_are_checked_before_network(self) -> None:
        model = analyze(self.source)
        self.source.write_text("def f(): pass\n", encoding="utf-8")
        with patch("formal_agent.ollama.urllib.request.build_opener") as builder:
            with self.assertRaisesRegex(ValueError, "Source changed"):
                OllamaProvider().propose(model)
            builder.assert_not_called()
        self.source.write_text(
            "def f():\n    return " + repr("x" * MAX_INPUT_BYTES) + "\n", encoding="utf-8"
        )
        with patch("formal_agent.ollama.urllib.request.build_opener") as builder:
            with self.assertRaisesRegex(ValueError, "input exceeds"):
                OllamaProvider().propose(analyze(self.source))
            builder.assert_not_called()


if __name__ == "__main__":
    unittest.main()
