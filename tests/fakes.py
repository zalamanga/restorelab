import hashlib
import json
import threading


def report_for(run_id, verified=True):
    def snapshot(numbers):
        rows = [{"order_id": number, "amount": number * 100} for number in numbers]
        canonical = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
        return {"row_count": len(rows), "fingerprint": hashlib.sha256(canonical).hexdigest()}

    return {
        "run_id": run_id,
        "scenario": "delete_orders",
        "strategy": "physical_backup_wal_named_target",
        "verified": verified,
        "backup": {"manifest_verified": True},
        "snapshots": {
            "base": snapshot(range(1, 101)),
            "before_incident": snapshot(range(1, 104)),
            "after_incident": snapshot(number for number in range(1, 104) if not 11 <= number <= 20),
            "restored": snapshot(range(1, 104)),
        },
        "recovery": {
            "target_name": "test_target_" + run_id,
            "target_lsn": "0/1600100",
            "wal_segment": "000000010000000000000001",
            "in_recovery": True,
            "paused": True,
            "replay_lsn": "0/1600100",
            "incident_marker_count": 0,
        },
        "measurements": {"recovery_seconds": 0.25, "drill_seconds": 0.5},
        "findings": {
            "recovered_ids": list(range(11, 21)),
            "post_backup_rows_verified": True,
            "incident_excluded": True,
        },
    }


class ControlledEngine:
    def __init__(self, blocked=False, error=None, verified=True):
        self.started = threading.Event()
        self.release = threading.Event()
        if not blocked:
            self.release.set()
        self.error = error
        self.verified = verified
        self.calls = []
        self.lock = threading.Lock()

    def execute(self, run_id, emit):
        with self.lock:
            self.calls.append(run_id)
        emit("prepare", "Test fixture engine started.")
        self.started.set()
        if not self.release.wait(timeout=10):
            raise TimeoutError("Test fixture gate was not released.")
        if self.error:
            raise self.error
        emit("verify", "Test fixture engine completed.", {"fixture": True})
        return report_for(run_id, self.verified)

    def health(self):
        return {"available": True, "fixture": True}
