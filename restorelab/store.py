import json
from pathlib import Path
import re
import sqlite3
from datetime import datetime, timezone
import uuid
from contextlib import contextmanager

RUN_ID = re.compile(r"[0-9a-f]{32}\Z")
STEPS = ("prepare", "backup", "verify_backup", "changes", "restore_point", "incident", "archive", "recover", "verify")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class StoreError(Exception):
    def __init__(self, code, message, status=409):
        self.code, self.status = code, status
        super().__init__(message)


class Store:
    def __init__(self, path, max_runs=5):
        if type(max_runs) is not int or not 1 <= max_runs <= 50:
            raise ValueError("max_runs must be between 1 and 50")
        self.path, self.max_runs = Path(path), max_runs
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                  id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
                  scenario TEXT NOT NULL, status TEXT NOT NULL,
                  step TEXT, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
                  message TEXT NOT NULL, error_json TEXT, report_json TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON runs((1))
                  WHERE status IN ('queued','running');
                CREATE TABLE IF NOT EXISTS events (
                  run_id TEXT NOT NULL REFERENCES runs(id), sequence INTEGER NOT NULL,
                  step TEXT NOT NULL, message TEXT NOT NULL, at TEXT NOT NULL,
                  details_json TEXT NOT NULL, PRIMARY KEY(run_id,sequence)
                );
            """)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        try:
            yield connection
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()

    def _detail(self, connection, row):
        if row is None:
            return None
        item = dict(row)
        error_json = item.pop("error_json")
        report_json = item.pop("report_json")
        item["error"] = json.loads(error_json) if error_json else None
        item["report"] = json.loads(report_json) if report_json else None
        item["events"] = []
        for event in connection.execute("SELECT sequence,step,message,at,details_json FROM events WHERE run_id=? ORDER BY sequence", (item["id"],)):
            value = dict(event)
            value["details"] = json.loads(value.pop("details_json"))
            item["events"].append(value)
        return item

    def create(self, request_id, scenario="delete_orders"):
        if not isinstance(request_id, str) or not RUN_ID.fullmatch(request_id) or scenario != "delete_orders":
            raise StoreError("invalid_request", "ID permintaan atau skenario tidak valid.", 400)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT * FROM runs WHERE request_id=?", (request_id,)).fetchone()
            if existing:
                result = self._detail(connection, existing)
                connection.commit()
                return result, True
            if connection.execute("SELECT 1 FROM runs WHERE status IN ('queued','running')").fetchone():
                raise StoreError("drill_active", "Masih ada latihan aktif. Tunggu sampai selesai.")
            used = connection.execute("SELECT count(*) FROM runs").fetchone()[0]
            if used >= self.max_runs:
                raise StoreError("run_limit", "Batas latihan tersimpan tercapai. Bukti lama tetap dipertahankan; lihat panduan pengelolaan lab.")
            identifier = uuid.uuid4().hex
            connection.execute("INSERT INTO runs(id,request_id,scenario,status,created_at,message) VALUES(?,?,?,'queued',?,?)", (identifier, request_id, scenario, utc_now(), "Latihan masuk antrean."))
            result = self._detail(connection, connection.execute("SELECT * FROM runs WHERE id=?", (identifier,)).fetchone())
            connection.commit()
            return result, False

    def claim(self):
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT id FROM runs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
            if row is None:
                connection.commit()
                return None
            connection.execute("UPDATE runs SET status='running',started_at=?,message=? WHERE id=? AND status='queued'", (utc_now(), "Latihan native PostgreSQL sedang berjalan.", row["id"]))
            result = self._detail(connection, connection.execute("SELECT * FROM runs WHERE id=?", (row["id"],)).fetchone())
            connection.commit()
            return result

    def append_event(self, run_id, step, message, details=None):
        if step not in STEPS or not isinstance(message, str) or not 1 <= len(message) <= 600:
            raise ValueError("Invalid workflow event")
        encoded = json.dumps(details if details is not None else {}, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > 16384:
            raise ValueError("Event evidence too large")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None or row["status"] != "running":
                raise StoreError("terminal_immutable", "Bukti latihan yang sudah selesai tidak dapat diubah.")
            sequence = connection.execute("SELECT COALESCE(max(sequence),0)+1 FROM events WHERE run_id=?", (run_id,)).fetchone()[0]
            if sequence > 100:
                raise ValueError("Too many workflow events")
            connection.execute("INSERT INTO events VALUES(?,?,?,?,?,?)", (run_id, sequence, step, message, utc_now(), encoded))
            connection.execute("UPDATE runs SET step=?,message=? WHERE id=?", (step, message, run_id))
            connection.commit()

    def finish(self, run_id, status, report=None, error=None):
        if status not in ("verified", "failed", "interrupted"):
            raise ValueError("Invalid terminal status")
        if (status == "verified") != isinstance(report, dict):
            raise ValueError("Only verified runs carry reports")
        report_json = json.dumps(report, ensure_ascii=False, allow_nan=False) if report is not None else None
        error_json = json.dumps(error, ensure_ascii=False, allow_nan=False) if error is not None else None
        messages = {"verified": "Data hasil pemulihan terverifikasi.", "failed": "Latihan gagal; bukti keberhasilan tidak diterbitkan.", "interrupted": "Latihan terhenti sebelum bukti selesai."}
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise StoreError("not_found", "Latihan tidak ditemukan.", 404)
            if row["status"] != "running":
                raise StoreError("terminal_immutable", "Bukti latihan yang sudah selesai tidak dapat diubah.")
            connection.execute("UPDATE runs SET status=?,finished_at=?,message=?,error_json=?,report_json=? WHERE id=?", (status, utc_now(), messages[status], error_json, report_json, run_id))
            result = self._detail(connection, connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
            connection.commit()
            return result

    def interrupt_running(self):
        error = json.dumps({"code": "controller_interrupted", "message": "Controller berhenti sebelum bukti selesai. Latihan tidak dijalankan ulang otomatis."})
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute("UPDATE runs SET status='interrupted',finished_at=?,message=?,error_json=? WHERE status IN ('queued','running')", (utc_now(), "Latihan terhenti sebelum bukti selesai.", error))
            count = cursor.rowcount
            connection.commit()
            return count

    def get(self, run_id):
        with self._connect() as connection:
            return self._detail(connection, connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())

    def list_runs(self):
        with self._connect() as connection:
            return [self._detail(connection, row) for row in connection.execute("SELECT * FROM runs ORDER BY created_at DESC,id DESC")]

    def state(self):
        with self._connect() as connection:
            connection.execute("BEGIN")
            runs = [self._detail(connection, row) for row in connection.execute("SELECT * FROM runs ORDER BY created_at DESC,id DESC")]
            active = next((row for row in runs if row["status"] in ("queued", "running")), None)
            connection.commit()
            return {"lab": {"name": "RestoreLab", "scope": "synthetic_only", "max_runs": self.max_runs, "used_runs": len(runs), "active_run_id": active["id"] if active else None}, "runs": runs, "active_run": active}
