from __future__ import annotations

import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from openai import APITimeoutError

from app import foundry


class FoundryDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        foundry.discover_endpoint.cache_clear()
        foundry.ensure_model.cache_clear()
        self.configured = patch.object(foundry.config, "FOUNDRY_ENDPOINT", "")
        self.configured.start()

    def tearDown(self) -> None:
        self.configured.stop()
        foundry.discover_endpoint.cache_clear()
        foundry.ensure_model.cache_clear()

    @staticmethod
    def result(stdout: str, code: int = 0) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess([], code, stdout=stdout, stderr="")

    def test_configured_valid_endpoint_does_not_invoke_cli(self) -> None:
        with (
            patch.object(foundry.config, "FOUNDRY_ENDPOINT", "http://127.0.0.1:4321"),
            patch.object(foundry, "_validate", return_value=True),
            patch.object(foundry.subprocess, "run") as run,
        ):
            self.assertEqual("http://127.0.0.1:4321", foundry.discover_endpoint())
            run.assert_not_called()

    def test_current_server_status_uses_utf8_and_caches_valid_endpoint(self) -> None:
        output = '{"state":"ready","webUrls":["http://127.0.0.1:64967"],"note":"\U0001f7e2"}'
        with (
            patch.object(foundry, "_validate", return_value=True) as validate,
            patch.object(foundry.subprocess, "run", return_value=self.result(output)) as run,
        ):
            self.assertEqual("http://127.0.0.1:64967", foundry.discover_endpoint())
            self.assertEqual("http://127.0.0.1:64967", foundry.discover_endpoint())
            run.assert_called_once_with(
                ["foundry", "server", "status", "--output", "json"],
                capture_output=True, text=True, encoding="utf-8", timeout=60,
            )
            validate.assert_called_once_with("http://127.0.0.1:64967")

    def test_legacy_status_is_supported_when_current_command_is_unavailable(self) -> None:
        results = [
            self.result("Unsupported command", 2),
            self.result("Service is Started on http://127.0.0.1:4321/"),
        ]
        with (
            patch.object(foundry, "_validate", return_value=True),
            patch.object(foundry.subprocess, "run", side_effect=results) as run,
            self.assertLogs("app.foundry", level="WARNING"),
        ):
            self.assertEqual("http://127.0.0.1:4321", foundry.discover_endpoint())
            self.assertEqual(["foundry", "service", "status"], run.call_args.args[0])
            self.assertEqual("utf-8", run.call_args.kwargs["encoding"])

    def test_decode_failure_is_logged_without_cli_output_and_tries_next_command(self) -> None:
        error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "RAW_OUTPUT_CANARY")
        with (
            patch.object(foundry, "_validate", return_value=True),
            patch.object(
                foundry.subprocess, "run",
                side_effect=[error, self.result("http://127.0.0.1:4321")],
            ),
            self.assertLogs("app.foundry", level="WARNING") as captured,
        ):
            self.assertEqual("http://127.0.0.1:4321", foundry.discover_endpoint())
        self.assertEqual("UnicodeDecodeError", captured.records[0].failure_type)
        self.assertNotIn("RAW_OUTPUT_CANARY", repr(captured.records[0].__dict__))

    def test_error_output_does_not_authorize_an_endpoint(self) -> None:
        with (
            patch.object(foundry, "_validate", return_value=False) as validate,
            patch.object(
                foundry.subprocess, "run",
                return_value=self.result("RAW_OUTPUT_CANARY http://127.0.0.1:9999", 2),
            ),
            self.assertLogs("app.foundry", level="WARNING") as captured,
        ):
            with self.assertRaisesRegex(RuntimeError, "foundry server start"):
                foundry.discover_endpoint()
        self.assertNotIn("RAW_OUTPUT_CANARY", repr([record.__dict__ for record in captured.records]))
        self.assertNotIn(("http://127.0.0.1:9999",), [call.args for call in validate.call_args_list])

    def test_missing_cli_can_still_use_valid_configured_candidate(self) -> None:
        with (
            patch.object(foundry, "_validate", return_value=True),
            patch.object(foundry, "_CANDIDATE_PORTS", [4321]),
            patch.object(foundry.subprocess, "run", side_effect=FileNotFoundError),
            self.assertLogs("app.foundry", level="WARNING"),
        ):
            self.assertEqual("http://127.0.0.1:4321", foundry.discover_endpoint())

    def test_lazy_model_loading_also_decodes_cli_output_as_utf8(self) -> None:
        with (
            patch.object(foundry, "_loaded_models", side_effect=[[], ["phi-4-mini-test"]]),
            patch.object(foundry.subprocess, "run", return_value=self.result("\U0001f7e2 Loaded")) as run,
        ):
            self.assertEqual("phi-4-mini-test", foundry.ensure_model("phi-4-mini"))
        run.assert_called_once_with(
            ["foundry", "model", "load", "phi-4-mini"],
            capture_output=True, text=True, encoding="utf-8", timeout=600,
        )

    def test_bounded_judge_call_disables_sdk_retries_without_changing_shared_client(self) -> None:
        shared = Mock()
        bounded = shared.with_options.return_value
        bounded.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="result"))],
        )
        with (
            patch.object(foundry, "ensure_model", return_value="phi-4-mini-test"),
            patch.object(foundry, "_local_client", return_value=shared),
        ):
            text, byte_count = foundry.chat_local(
                "phi-4-mini", [{"role": "user", "content": "hello"}],
                max_tokens=400, temperature=0.0, timeout_seconds=12.5,
            )
        self.assertEqual("result", text)
        self.assertEqual(11, byte_count)
        shared.with_options.assert_called_once_with(timeout=12.5, max_retries=0)
        shared.chat.completions.create.assert_not_called()
        bounded.chat.completions.create.assert_called_once()

    def test_unbounded_legacy_call_keeps_existing_client_options(self) -> None:
        shared = Mock()
        shared.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="result"))],
        )
        with (
            patch.object(foundry, "ensure_model", return_value="phi-4-mini-test"),
            patch.object(foundry, "_local_client", return_value=shared),
        ):
            foundry.chat_local("phi-4-mini", [{"role": "user", "content": "hello"}])
        shared.with_options.assert_not_called()
        shared.chat.completions.create.assert_called_once()

    def test_bounded_judge_call_makes_only_one_transport_attempt_on_timeout(self) -> None:
        attempts = []
        release = Event()

        class StalledHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                attempts.append(self.path)
                release.wait(timeout=2)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), StalledHandler)
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            with (
                foundry.OpenAI(
                    base_url=f"http://127.0.0.1:{server.server_port}/v1", api_key="not-needed",
                ) as client,
                patch.object(foundry, "ensure_model", return_value="phi-4-mini-test"),
                patch.object(foundry, "_local_client", return_value=client),
            ):
                with self.assertRaises(APITimeoutError):
                    foundry.chat_local(
                        "phi-4-mini", [{"role": "user", "content": "hello"}],
                        timeout_seconds=0.1,
                    )
        finally:
            release.set()
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()
        self.assertEqual(1, len(attempts))

    def test_judge_timeout_configuration_is_finite_and_positive(self) -> None:
        for invalid in ("0", "-1", "nan", "inf", "-inf", "not-a-number"):
            with self.subTest(value=invalid), patch.dict(
                foundry.config.os.environ, {"JUDGE_TIMEOUT_SECONDS": invalid},
            ):
                with self.assertRaisesRegex(ValueError, "finite positive"):
                    foundry.config._judge_timeout_seconds()
        with patch.dict(foundry.config.os.environ, {"JUDGE_TIMEOUT_SECONDS": "12.5"}):
            self.assertEqual(12.5, foundry.config._judge_timeout_seconds())


if __name__ == "__main__":
    unittest.main()
