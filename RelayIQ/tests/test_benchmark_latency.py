from __future__ import annotations

import unittest

from eval.benchmark_latency import websocket_url, with_samples


class LatencyBenchmarkTests(unittest.TestCase):
    def test_websocket_url_tracks_http_security(self) -> None:
        self.assertEqual("ws://127.0.0.1:8000/ws", websocket_url("http://127.0.0.1:8000"))
        self.assertEqual("wss://example.test/ws", websocket_url("https://example.test"))

    def test_latency_summary_includes_sample_count(self) -> None:
        summary = with_samples([1.0, 2.0, 3.0])

        self.assertEqual(3, summary["samples"])
        self.assertEqual(2.0, summary["mean"])
        self.assertEqual(2.9, summary["p95"])


if __name__ == "__main__":
    unittest.main()