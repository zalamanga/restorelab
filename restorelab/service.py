import json
import logging
import math
import os
from pathlib import Path
import re
import threading

from .store import Store, StoreError


class OwnerLock:
    def __init__(self, path):
        self.path, self.handle = Path(path), None
        self._mutex = threading.Lock()

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            if self.path.stat().st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise RuntimeError("RestoreLab controller lain masih memiliki direktori data ini.") from None
        self.handle = handle

    def close(self):
        with self._mutex:
            if self.handle is not None:
                self.handle.close()
                self.handle = None


def validate_report(report, run_id):
    try:
        if not isinstance(report, dict) or report["run_id"] != run_id or report["verified"] is not True:
            raise ValueError()
        if report["scenario"] != "delete_orders" or report["strategy"] != "physical_backup_wal_named_target":
            raise ValueError()
        if report["backup"]["manifest_verified"] is not True:
            raise ValueError()
        snapshots = report["snapshots"]
        for name, count in (("base",100),("before_incident",103),("after_incident",93),("restored",103)):
            snapshot = snapshots[name]
            if type(snapshot["row_count"]) is not int or snapshot["row_count"] != count or not re.fullmatch(r"[0-9a-f]{64}", snapshot["fingerprint"]):
                raise ValueError()
        if snapshots["restored"]["fingerprint"] != snapshots["before_incident"]["fingerprint"] or snapshots["restored"]["fingerprint"] == snapshots["after_incident"]["fingerprint"]:
            raise ValueError()
        if snapshots["base"]["fingerprint"] == snapshots["before_incident"]["fingerprint"]:
            raise ValueError()
        recovery, findings = report["recovery"], report["findings"]
        if recovery["paused"] is not True or recovery["in_recovery"] is not True or type(recovery["incident_marker_count"]) is not int or recovery["incident_marker_count"] != 0:
            raise ValueError()
        if run_id not in recovery["target_name"] or not re.fullmatch(r"[0-9A-F]{24}", recovery["wal_segment"]):
            raise ValueError()
        for name in ("target_lsn", "replay_lsn"):
            if not re.fullmatch(r"[0-9A-F]+/[0-9A-F]+", recovery[name]):
                raise ValueError()
        if findings["recovered_ids"] != list(range(11,21)) or any(type(value) is not int for value in findings["recovered_ids"]) or findings["post_backup_rows_verified"] is not True or findings["incident_excluded"] is not True:
            raise ValueError()
        for name in ("recovery_seconds", "drill_seconds"):
            value = report["measurements"][name]
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError()
        if len(json.dumps(report, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 262144:
            raise ValueError()
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
        raise ValueError("Bukti pemulihan tidak memenuhi pemeriksaan akhir.") from None


class Service:
    def __init__(self, root, *, data_dir=None, engine=None, max_runs=5):
        self.root = Path(root).resolve()
        self.data_dir = Path(data_dir).resolve() if data_dir is not None else self.root / ".data"
        self.store = Store(self.data_dir / "controller.sqlite", max_runs=max_runs)
        if engine is None:
            from .engine import Engine
            engine = Engine(self.root)
        self.engine = engine
        self._owner = OwnerLock(self.data_dir / "owner.lock")
        self._wake, self._stop = threading.Event(), threading.Event()
        self._thread = None

    def start(self):
        if self._thread is not None:
            raise RuntimeError("Service already started")
        self._owner.acquire()
        try:
            self.store.interrupt_running()
            self._thread = threading.Thread(target=self._work, name="restorelab-worker", daemon=False)
            self._thread.start()
        except Exception:
            self._owner.close()
            raise

    def close(self):
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=20)
            if self._thread.is_alive():
                return
        self._owner.close()

    def enqueue(self, request_id, scenario="delete_orders"):
        if self._stop.is_set():
            raise StoreError("stopping", "Controller sedang berhenti.", 503)
        if self._thread is None or not self._thread.is_alive():
            raise StoreError("worker_unavailable", "Worker belum tersedia. Mulai ulang controller RestoreLab.", 503)
        result = self.store.create(request_id, scenario)
        self._wake.set()
        return result

    def state(self):
        result = self.store.state()
        result["lab"]["worker_available"] = self._thread is not None and self._thread.is_alive() and not self._stop.is_set()
        return result

    def get_run(self, run_id):
        run = self.store.get(run_id)
        if run is None:
            raise StoreError("not_found", "Latihan tidak ditemukan.", 404)
        return run

    def get_report(self, run_id):
        run = self.get_run(run_id)
        if run["status"] != "verified" or run["report"] is None:
            raise StoreError("report_unavailable", "Laporan baru tersedia setelah bukti pemulihan terverifikasi.")
        return run["report"]

    def _work(self):
        try:
            while not self._stop.is_set():
                run = self.store.claim()
                if run is None:
                    self._wake.wait(timeout=1)
                    self._wake.clear()
                    continue
                run_id = run["id"]
                def emit(step, message, details=None):
                    if self._stop.is_set():
                        raise InterruptedError("Controller stopping")
                    self.store.append_event(run_id, step, message, details)
                try:
                    report = self.engine.execute(run_id, emit)
                    if self._stop.is_set():
                        raise InterruptedError("Controller stopping")
                    validate_report(report, run_id)
                    self.store.finish(run_id, "verified", report=report)
                except InterruptedError:
                    self.store.finish(run_id, "interrupted", error={"code":"controller_interrupted","message":"Controller berhenti sebelum bukti selesai. Latihan tidak dijalankan ulang otomatis."})
                except Exception as error:
                    code = getattr(error, "code", "verification_failed")
                    if not isinstance(code, str) or not re.fullmatch(r"[a-z_]{1,64}", code):
                        code = "native_failure"
                    message = str(error) if hasattr(error, "code") else "Bukti latihan belum memenuhi pemeriksaan akhir. Lihat panduan diagnosis lokal."
                    self.store.finish(run_id, "failed", error={"code":code,"message":message[:600]})
                    logging.error("RestoreLab run=%s failed code=%s", run_id, code)
        finally:
            self._owner.close()
