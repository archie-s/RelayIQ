from __future__ import annotations

import unittest

from eval.evaluate_judge import classification_metrics, latency_summary


class EvaluationMetricsTests(unittest.TestCase):
    def test_classification_metrics_include_macro_and_per_label_results(self) -> None:
        metrics = classification_metrics(
            ["cloud", "local", "cloud", "local"],
            ["cloud", "cloud", "local", "local"],
        )

        self.assertEqual(0.5, metrics["accuracy"])
        self.assertEqual(0.5, metrics["macro_precision"])
        self.assertEqual(0.5, metrics["macro_recall"])
        self.assertEqual(0.5, metrics["macro_f1"])
        self.assertEqual(2, metrics["per_label"]["cloud"]["support"])

    def test_latency_summary_interpolates_percentiles(self) -> None:
        summary = latency_summary([1.0, 2.0, 3.0, 4.0])

        self.assertEqual(2.5, summary["mean"])
        self.assertEqual(2.5, summary["p50"])
        self.assertEqual(3.85, summary["p95"])
        self.assertEqual(4.0, summary["max"])


if __name__ == "__main__":
    unittest.main()