#!/usr/bin/env python3
"""Regression checks for process-tree traversal and CPU sample completeness."""
import sys
sys.dont_write_bytecode = True
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import bench_linux as bench

ROOT = Path(__file__).resolve().parent.parent

class BenchmarkAccountingTests(unittest.TestCase):
    def setUp(self):
        self.before = {"cpu_ticks": {"11:123": 10, "12:456": 2},
                       "elapsed_seconds": 0, "unread_processes": []}
        self.after = {"cpu_ticks": {"11:123": 15, "12:456": 5},
                      "elapsed_seconds": 1, "unread_processes": []}

    def test_known_child_does_not_hide_new_grandchild(self):
        evidence = ROOT / ".test-evidence"
        evidence.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="proc-regression-", dir=evidence) as tmp:
            root = Path(tmp)
            for pid, children in [(100, "101"), (101, "102"), (102, "")]:
                target = root / str(pid) / "task" / str(pid)
                target.mkdir(parents=True)
                (target / "children").write_text(children)
            def proc_path(path):
                raw = str(path)
                if raw.startswith("/proc"):
                    return root / raw.removeprefix("/proc").lstrip("/")
                return Path(path)
            with patch.object(bench, "Path", side_effect=proc_path), patch.object(
                bench.os, "getsid", side_effect=lambda pid: 100 if pid in (100, 101) else 999
            ):
                self.assertEqual(bench.family(100, {101}), {100, 101, 102})

    def test_real_sample_separates_main_and_total_without_dropping_missing(self):
        snapshot = bench.sample({bench.os.getpid(), 2147483647})
        main = bench.main_process_memory(snapshot, bench.os.getpid())
        self.assertIsNotNone(main)
        self.assertGreater(main["rss_kib"], 0)
        self.assertGreater(main["pss_kib"], 0)
        self.assertIn(2147483647, snapshot["unread_processes"])
        self.assertEqual(snapshot["rss_kib"], sum(x["rss_kib"] for x in snapshot["process_memory"].values()))
        self.assertIsNone(bench.complete_main_median(
            [{"main_process": main}, {"main_process": None}], "pss_kib"))

    def test_display_delta_retains_cost_and_rejects_replacement(self):
        before = {"unread_processes": [], "processes": 1, "cpu_ticks": {"2:100": 1},
                  "rss_kib": 5000, "pss_kib": 3000, "swap_kib": 0, "swap_pss_kib": 0}
        after = dict(before, rss_kib=8000, pss_kib=6000)
        self.assertEqual(bench.display_change([{"display_server": after}], before)["pss_kib"], 3000)
        after["cpu_ticks"] = {"2:200": 1}
        self.assertIsNone(bench.display_change([{"display_server": after}], before))

    def test_stable_processes_report_complete_cpu(self):
        result = bench.summarize_cpu([self.before, self.after], 100)
        self.assertTrue(result["cpu_process_set_stable_at_sample_points"])
        self.assertEqual(result["idle_cpu_seconds"], 0.08)

    def test_entering_or_exiting_process_invalidates_complete_cpu(self):
        self.after["cpu_ticks"] = {"11:123": 15, "13:789": 900}
        result = bench.summarize_cpu([self.before, self.after], 100)
        self.assertFalse(result["cpu_process_set_stable_at_sample_points"])
        self.assertIsNone(result["idle_cpu_seconds"])
        self.assertEqual(result["observed_common_cpu_seconds"], 0.05)

    def test_pid_reuse_is_a_different_process(self):
        self.after["cpu_ticks"] = {"11:999": 500, "12:456": 5}
        self.assertIsNone(bench.summarize_cpu([self.before, self.after], 100)["idle_cpu_seconds"])

    def test_incomplete_read_invalidates_complete_cpu(self):
        self.after["unread_processes"] = [99]
        self.assertIsNone(bench.summarize_cpu([self.before, self.after], 100)["idle_cpu_seconds"])

if __name__ == "__main__":
    unittest.main(verbosity=2)
