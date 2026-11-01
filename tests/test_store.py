import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest
import uuid

from restorelab.store import Store, StoreError
from tests.fakes import report_for


class StoreTests(unittest.TestCase):
    def setUp(self):
        temporary_root = Path.cwd() / ".local" / "test-data"
        temporary_root.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix="store-", dir=temporary_root)
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "controller.sqlite"
        self.store = Store(self.path)

    def create(self, request_id=None):
        return self.store.create(request_id or uuid.uuid4().hex)

    def complete(self, request_id=None):
        run, replayed = self.create(request_id)
        self.assertFalse(replayed)
        claimed = self.store.claim()
        self.assertEqual(claimed["id"], run["id"])
        return self.store.finish(run["id"], "verified", report=report_for(run["id"]))

    def test_empty_ledger_has_contract_envelope_and_default_quota(self):
        state = self.store.state()
        self.assertEqual(state["lab"]["name"], "RestoreLab")
        self.assertEqual(state["lab"]["scope"], "synthetic_only")
        self.assertEqual(state["lab"]["max_runs"], 5)
        self.assertEqual(state["lab"]["used_runs"], 0)
        self.assertIsNone(state["lab"]["active_run_id"])
        self.assertEqual(state["runs"], [])
        self.assertIsNone(state["active_run"])

    def test_create_records_queued_request_without_inventing_success(self):
        request_id = uuid.uuid4().hex
        run, replayed = self.create(request_id)
        self.assertFalse(replayed)
        self.assertRegex(run["id"], r"^[0-9a-f]{32}$")
        self.assertEqual(run["request_id"], request_id)
        self.assertEqual(run["status"], "queued")
        self.assertIsNone(run["started_at"])
        self.assertIsNone(run["finished_at"])
        self.assertIsNone(run["report"])
        self.assertEqual(self.store.state()["lab"]["active_run_id"], run["id"])

    def test_duplicate_request_reuses_queued_job_without_spending_attempt(self):
        request_id = uuid.uuid4().hex
        original, _ = self.create(request_id)
        duplicate, replayed = self.create(request_id)
        self.assertTrue(replayed)
        self.assertEqual(duplicate, original)
        self.assertEqual(self.store.state()["lab"]["used_runs"], 1)

    def test_distinct_request_cannot_join_an_active_job(self):
        original, _ = self.create()
        with self.assertRaises(StoreError) as raised:
            self.create()
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(self.store.state()["lab"]["used_runs"], 1)
        self.assertEqual(self.store.state()["lab"]["active_run_id"], original["id"])

    def test_invalid_request_identity_is_rejected_without_quota_change(self):
        for request_id in ("", "A" * 32, "g" * 32, "a" * 31, "a" * 33, str(uuid.uuid4()), None, 123):
            with self.subTest(request_id=request_id), self.assertRaises(StoreError):
                self.store.create(request_id)
        self.assertEqual(self.store.state()["lab"]["used_runs"], 0)

    def test_scenario_is_fixed_before_a_job_is_created(self):
        with self.assertRaises(StoreError):
            self.store.create(uuid.uuid4().hex, "arbitrary_sql")
        self.assertEqual(self.store.list_runs(), [])

    def test_claim_transitions_once_and_timestamps_are_persisted(self):
        run, _ = self.create()
        claimed = self.store.claim()
        self.assertEqual(claimed["id"], run["id"])
        self.assertEqual(claimed["status"], "running")
        self.assertTrue(claimed["started_at"])
        self.assertIsNone(self.store.claim())
        reopened = Store(self.path)
        self.assertEqual(reopened.get(run["id"])["started_at"], claimed["started_at"])

    def test_only_one_thread_can_claim_the_queued_job(self):
        run, _ = self.create()
        barrier = threading.Barrier(8)
        def claim():
            barrier.wait(timeout=5)
            return self.store.claim()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: claim(), range(8)))
        claimed = [result for result in results if result is not None]
        self.assertEqual([item["id"] for item in claimed], [run["id"]])

    def test_events_have_durable_sequence_and_copy_details(self):
        run, _ = self.create()
        self.store.claim()
        details = {"rows": [100], "fixture": True}
        self.store.append_event(run["id"], "prepare", "Synthetic preparation", details)
        details["rows"].append(999)
        self.store.append_event(run["id"], "backup", "Synthetic backup")
        events = Store(self.path).get(run["id"])["events"]
        self.assertEqual([item["sequence"] for item in events], [1, 2])
        self.assertEqual(events[0]["details"], {"rows": [100], "fixture": True})
        self.assertEqual(events[1]["step"], "backup")
        self.assertTrue(all(item["at"] for item in events))

    def test_verified_report_survives_reopening_without_mutation(self):
        completed = self.complete()
        expected = copy.deepcopy(completed)
        completed["report"]["verified"] = False
        self.assertEqual(Store(self.path).get(expected["id"]), expected)
        self.assertIsNone(self.store.state()["lab"]["active_run_id"])

    def test_failed_attempt_is_persistent_and_spends_quota(self):
        run, _ = self.create()
        self.store.claim()
        failure = {"code": "fixture_failure", "message": "Synthetic controlled failure"}
        completed = self.store.finish(run["id"], "failed", error=failure)
        self.assertEqual(completed["status"], "failed")
        self.assertIsNone(completed["report"])
        self.assertEqual(Store(self.path).get(run["id"])["error"], failure)
        self.assertEqual(self.store.state()["lab"]["used_runs"], 1)

    def test_terminal_events_and_results_are_immutable(self):
        completed = self.complete()
        for mutate in (
            lambda: self.store.append_event(completed["id"], "incident", "Late event"),
            lambda: self.store.finish(completed["id"], "failed", error={"code": "late", "message": "Late"}),
        ):
            with self.subTest(operation=mutate), self.assertRaises(StoreError):
                mutate()
        self.assertEqual(self.store.get(completed["id"]), completed)

    def test_completed_request_replay_returns_original_immutable_evidence(self):
        request_id = uuid.uuid4().hex
        completed = self.complete(request_id)
        duplicate, replayed = Store(self.path).create(request_id)
        self.assertTrue(replayed)
        self.assertEqual(duplicate, completed)
        self.assertEqual(self.store.state()["lab"]["used_runs"], 1)

    def test_quota_persists_and_replay_still_works_after_exhaustion(self):
        completed = [self.complete() for _ in range(5)]
        reopened = Store(self.path)
        with self.assertRaises(StoreError) as raised:
            reopened.create(uuid.uuid4().hex)
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(reopened.state()["lab"]["used_runs"], 5)
        replay, duplicate = reopened.create(completed[0]["request_id"])
        self.assertTrue(duplicate)
        self.assertEqual(replay["id"], completed[0]["id"])

    def test_same_request_race_creates_exactly_one_ledger_row(self):
        request_id = uuid.uuid4().hex
        barrier = threading.Barrier(8)
        def create():
            barrier.wait(timeout=5)
            return self.store.create(request_id)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: create(), range(8)))
        self.assertEqual(len({run["id"] for run, _ in results}), 1)
        self.assertEqual(sum(not replayed for _, replayed in results), 1)
        self.assertEqual(len(self.store.list_runs()), 1)
        self.assertEqual(self.store.state()["lab"]["used_runs"], 1)

    def test_distinct_request_race_accepts_only_one_active_job(self):
        barrier = threading.Barrier(8)
        def create():
            barrier.wait(timeout=5)
            try:
                return "created", self.store.create(uuid.uuid4().hex)
            except StoreError as error:
                return "blocked", error.status
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: create(), range(8)))
        self.assertEqual(sum(kind == "created" for kind, _ in results), 1)
        self.assertTrue(all(value == 409 for kind, value in results if kind == "blocked"))
        self.assertEqual(self.store.state()["lab"]["used_runs"], 1)

    def test_interruption_does_not_rerun_or_overwrite_terminal_history(self):
        completed = self.complete()
        active, _ = self.create()
        self.store.claim()
        reopened = Store(self.path)
        self.assertEqual(reopened.interrupt_running(), 1)
        interrupted = reopened.get(active["id"])
        self.assertEqual(interrupted["status"], "interrupted")
        self.assertTrue(interrupted["finished_at"])
        self.assertIsNone(reopened.claim())
        self.assertEqual(reopened.get(completed["id"]), completed)
        self.assertEqual(reopened.interrupt_running(), 0)

    def test_unknown_job_cannot_receive_events_or_terminal_result(self):
        unknown = uuid.uuid4().hex
        self.assertIsNone(self.store.get(unknown))
        with self.assertRaises(StoreError):
            self.store.append_event(unknown, "prepare", "Unknown")
        with self.assertRaises(StoreError) as raised:
            self.store.finish(unknown, "failed")
        self.assertEqual(raised.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
