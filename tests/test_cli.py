import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from call_logs_processor import NormalisationError
from call_logs_processor.cli import run
from call_logs_processor.file_reader import read_and_normalise


FIXTURES = Path(__file__).parent / "fixtures"


class FileTests(unittest.TestCase):
    def test_file_reader_accepts_paths_and_strings(self):
        fixture = FIXTURES / "twilio_rest.json"

        for file in (fixture, str(fixture)):
            with self.subTest(file=file):
                calls = read_and_normalise(file)

                self.assertEqual(len(calls), 4)

    def test_file_reader_reports_invalid_input(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "calls.txt"

            for payload in ("unrecognised format", "{broken"):
                file.write_text(payload, encoding="utf-8")

                with self.subTest(payload=payload), self.assertRaises(NormalisationError):
                    read_and_normalise(file)

            with self.assertRaises(OSError):
                read_and_normalise(Path(directory) / "missing.json")


class CLITests(unittest.TestCase):
    def _run_cli(self, file):
        stdout, stderr = io.StringIO(), io.StringIO()

        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
            patch("sys.argv", ["call-log-normalise", str(file)]),
        ):
            code = run()

        return code, stdout.getvalue(), stderr.getvalue()

    def test_auto_detects_each_provider_file(self):
        for filename in ("asterisk.csv", "twilio_rest.json", "twilio_webhooks.json", "ringcentral.json"):
            with self.subTest(filename=filename):
                code, stdout, stderr = self._run_cli(FIXTURES / filename)

                self.assertEqual(code, 0, stderr)
                self.assertEqual(stderr, "")

                calls = json.loads(stdout)
                self.assertEqual(len(calls), 4)

                if filename == "asterisk.csv":
                    self.assertEqual(calls[0]["start"], "2026-10-24T08:00:00Z")

    def test_invalid_input_fails_without_partial_json_or_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.json"
            invalid.write_text("{broken", encoding="utf-8")

            for file in (invalid, Path(directory) / "missing.json"):
                with self.subTest(file=file):
                    code, stdout, stderr = self._run_cli(file)

                    self.assertEqual(code, 2)
                    self.assertEqual(stdout, "")
                    self.assertIn(str(file), stderr)
                    self.assertNotIn("Traceback", stderr)


if __name__ == "__main__":
    unittest.main()
