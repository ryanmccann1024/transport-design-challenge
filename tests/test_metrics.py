import unittest

from netlab.metrics import command_metrics


class MetricsChecks(unittest.TestCase):
    def test_percentiles_use_nearest_rank_and_keep_misses_visible(self):
        records = {str(i): {"status": "delivered", "latency_ms": i} for i in range(1, 121)}
        records["miss"] = {"status": "expired", "latency_ms": None}
        metrics = command_metrics(records)
        self.assertEqual([metrics[f"p{p}_ms"] for p in (50, 90, 99)], [60, 108, 119])
        self.assertEqual(metrics["expired"], 1)

    def test_no_deliveries_leave_percentiles_undefined(self):
        metrics = command_metrics({"waiting": {"status": "pending", "latency_ms": None}})
        self.assertIsNone(metrics["p99_ms"])
        self.assertEqual(metrics["pending"], 1)
