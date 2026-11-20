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
