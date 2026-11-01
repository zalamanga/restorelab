import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

from restorelab.engine import EngineError
from restorelab.service import Service
from restorelab.store import Store, StoreError
from tests.fakes import ControlledEngine


class AlteredReportEngine(ControlledEngine):
    def __init__(self, alter):
        super().__init__()
        self.alter = alter

    def execute(self, run_id, emit):
        report = super().execute(run_id, emit)
        self.alter(report)
        return report


class ServiceTests(unittest.TestCase):
    def setUp(self):
        temporary_root = Path.cwd() / ".local" / "test-data"
        temporary_root.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix="service-", dir=temporary_root)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.services = []
        self.addCleanup(self.close_services)

    def close_services(self):
        for service, engine in reversed(self.services):
            engine.release.set()
            service.close()

    def service(self, engine=None, root=None):
        engine = engine or ControlledEngine()
        service = Service(root or self.root, engine=engine)
        self.services.append((service, engine))
        service.start()
        return service

    def terminal(self, service, run_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            run = service.get_run(run_id)
            if run["status"] not in ("queued", "running"):
                return run
            time.sleep(0.01)
        self.fail("The controlled test engine did not reach a terminal state")

    def test_worker_executes_accepted_job_once_and_persists_evidence(self):
        engine = ControlledEngine()
        service = self.service(engine)
        request_id = uuid.uuid4().hex
        run, replayed = service.enqueue(request_id)
        self.assertFalse(replayed)
        completed = self.terminal(service, run["id"])
        self.assertEqual(completed["status"], "verified")
        self.assertEqual(engine.calls, [run["id"]])
        self.assertEqual(service.get_report(run["id"]), completed["report"])
        sequences = [event["sequence"] for event in completed["events"]]
        self.assertEqual(sequences, list(range(1, len(sequences) + 1)))
        self.assertTrue({"prepare", "verify"} <= {event["step"] for event in completed["events"]})
        replay, duplicate = service.enqueue(request_id)
        self.assertTrue(duplicate)
        self.assertEqual(replay, completed)
        self.assertEqual(engine.calls, [run["id"]])

    def test_active_job_does_not_expose_an_early_report(self):
        engine = ControlledEngine(blocked=True)
        service = self.service(engine)
        run, _ = service.enqueue(uuid.uuid4().hex)
        self.assertTrue(engine.started.wait(timeout=3))
        with self.assertRaises(StoreError) as raised:
            service.get_report(run["id"])
        self.assertEqual(raised.exception.status, 409)
        self.assertIsNone(service.get_run(run["id"])["report"])

    def test_engine_controlled_failure_is_terminal_without_success_report(self):
        service = self.service(ControlledEngine(error=EngineError("fixture_failed", "Kegagalan sintetis terkontrol.")))
        run, _ = service.enqueue(uuid.uuid4().hex)
        failed = self.terminal(service, run["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertIsNone(failed["report"])
        self.assertEqual(failed["error"]["code"], "fixture_failed")
        with self.assertRaises(StoreError):
            service.get_report(run["id"])

    def test_unexpected_engine_exception_does_not_publish_private_stderr(self):
        sentinel = "PRIVATE-STDERR-SHOULD-NOT-BE-PUBLIC"
        service = self.service(ControlledEngine(error=RuntimeError(sentinel)))
        run, _ = service.enqueue(uuid.uuid4().hex)
        failed = self.terminal(service, run["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertNotIn(sentinel, str(failed))
        self.assertNotIn("Traceback", str(failed))
        self.assertNotIn(sentinel, str(service.state()))

    def test_unverified_engine_report_cannot_become_verified_status(self):
        service = self.service(ControlledEngine(verified=False))
        run, _ = service.enqueue(uuid.uuid4().hex)
        completed = self.terminal(service, run["id"])
        self.assertEqual(completed["status"], "failed")
        self.assertIsNone(completed["report"])

    def test_counts_alone_cannot_replace_content_and_recovery_evidence(self):
        alterations = {
            "wrong_run": lambda report: report.update(run_id="f" * 32),
            "wrong_count": lambda report: report["snapshots"]["restored"].update(row_count=100),
            "wrong_fingerprint": lambda report: report["snapshots"]["restored"].update(fingerprint="0" * 64),
            "base_not_distinguished": lambda report: report["snapshots"]["base"].update(fingerprint=report["snapshots"]["before_incident"]["fingerprint"]),
            "missing_post_backup": lambda report: report["findings"].update(post_backup_rows_verified=False),
            "missing_deleted_order": lambda report: report["findings"].update(recovered_ids=list(range(11, 20))),
            "floating_order_ids": lambda report: report["findings"].update(recovered_ids=[float(number) for number in range(11, 21)]),
            "incident_replayed": lambda report: report["recovery"].update(incident_marker_count=1),
            "not_paused": lambda report: report["recovery"].update(paused=False),
            "not_in_recovery": lambda report: report["recovery"].update(in_recovery=False),
        }
        for index, (name, alter) in enumerate(alterations.items()):
            with self.subTest(evidence=name):
                service = self.service(AlteredReportEngine(alter), self.root / f"case-{index}")
                run, _ = service.enqueue(uuid.uuid4().hex)
                completed = self.terminal(service, run["id"])
                self.assertEqual(completed["status"], "failed")
                self.assertIsNone(completed["report"])

    def test_restart_marks_old_running_work_interrupted_without_rerun(self):
        old_store = Store(self.root / ".data" / "controller.sqlite")
        old, _ = old_store.create(uuid.uuid4().hex)
        old_store.claim()
        old_store.append_event(old["id"], "backup", "Synthetic prior process reached backup")
        engine = ControlledEngine()
        service = self.service(engine)
        interrupted = service.get_run(old["id"])
        self.assertEqual(interrupted["status"], "interrupted")
        self.assertEqual(engine.calls, [])
        self.assertEqual(interrupted["events"][0]["step"], "backup")
        self.assertEqual(service.state()["lab"]["used_runs"], 1)

    def test_restart_does_not_silently_execute_an_old_queued_attempt(self):
        old_store = Store(self.root / ".data" / "controller.sqlite")
        old, _ = old_store.create(uuid.uuid4().hex)
        engine = ControlledEngine()
        service = self.service(engine)
        interrupted = service.get_run(old["id"])
        self.assertEqual(interrupted["status"], "interrupted")
        self.assertEqual(engine.calls, [])

    def test_restart_preserves_terminal_result_and_deduplication(self):
        first = self.service()
        request_id = uuid.uuid4().hex
        run, _ = first.enqueue(request_id)
        completed = copy.deepcopy(self.terminal(first, run["id"]))
        first.close()
        second_engine = ControlledEngine()
        second = self.service(second_engine)
        replay, duplicate = second.enqueue(request_id)
        self.assertTrue(duplicate)
        self.assertEqual(replay, completed)
        self.assertEqual(second_engine.calls, [])

    def test_missing_job_report_is_not_found(self):
        service = self.service()
        with self.assertRaises(StoreError) as raised:
            service.get_report(uuid.uuid4().hex)
        self.assertEqual(raised.exception.status, 404)

    def test_controller_without_a_live_worker_cannot_reserve_an_attempt(self):
        engine = ControlledEngine()
        service = Service(self.root, engine=engine)
        self.addCleanup(service.close)
        with self.assertRaises(StoreError) as raised:
            service.enqueue(uuid.uuid4().hex)
        self.assertEqual(raised.exception.status, 503)
        service.start()
        service.close()
        with self.assertRaises(StoreError) as raised:
            service.enqueue(uuid.uuid4().hex)
        self.assertEqual(raised.exception.status, 503)
        self.assertEqual(service.state()["lab"]["used_runs"], 0)

    def test_competing_controller_cannot_interrupt_the_current_owner(self):
        engine = ControlledEngine(blocked=True)
        service = self.service(engine)
        run, _ = service.enqueue(uuid.uuid4().hex)
        self.assertTrue(engine.started.wait(timeout=3))
        competing = Service(self.root, engine=ControlledEngine())
        self.addCleanup(competing.close)
        with self.assertRaises(RuntimeError):
            competing.start()
        self.assertEqual(service.get_run(run["id"])["status"], "running")
        self.assertEqual(engine.calls, [run["id"]])

    def test_owner_lock_excludes_an_independent_python_process(self):
        self.service()
        child = """import json, sys
from restorelab.service import OwnerLock
lock = OwnerLock(sys.argv[1])
try:
    lock.acquire()
except RuntimeError:
    print(json.dumps({'blocked': True}))
else:
    lock.close()
    print(json.dumps({'blocked': False}))
"""
        result = subprocess.run(
            [sys.executable, "-c", child, str(self.root / ".data" / "owner.lock")],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"blocked": True})


if __name__ == "__main__":
    unittest.main()
