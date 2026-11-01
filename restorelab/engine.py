"""Guarded physical PostgreSQL recovery drill; no HTTP-controlled SQL or paths."""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess
import sys
import threading
import time


class EngineError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


_EXECUTION_LOCK = threading.Lock()
_RUN_ID = re.compile(r"[0-9a-f]{32}\Z")
_LSN = re.compile(r"[0-9A-F]+/[0-9A-F]+\Z")
_WAL = re.compile(r"[0-9A-F]{24}\Z")
_LAB_ID = "restorelab-synthetic-v1"


class Engine:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self._stage = "setup"
        self._diagnostic_lock = threading.Lock()

    def _record_native_error(self, arguments, *, run_id, stdout=None, stderr=None, returncode=None, timed_out=False, fatal=True):
        try:
            secret_path = self.root / ".secrets" / "postgres_password"
            secret = secret_path.read_text(encoding="utf-8").strip() if secret_path.is_file() else ""

            def safe_text(value, limit=8192):
                value = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value or "")
                if secret:
                    value = value.replace(secret, "[REDACTED]")
                return value.encode("utf-8")[-limit:].decode("utf-8", errors="replace")

            evidence = {
                "at": datetime.now(timezone.utc).isoformat(), "stage": self._stage,
                "run_id": run_id, "argv": [safe_text(argument, 1024) for argument in arguments],
                "returncode": returncode, "timed_out": timed_out, "fatal": fatal,
                "stdout": safe_text(stdout), "stderr": safe_text(stderr),
            }
            encoded = json.dumps(evidence, ensure_ascii=False, indent=2).encode("utf-8")
            while len(encoded) > 32768 and (evidence["stdout"] or evidence["stderr"]):
                stream = max(("stdout", "stderr"), key=lambda name: len(json.dumps(evidence[name], ensure_ascii=False).encode("utf-8")))
                evidence[stream] = evidence[stream][(len(evidence[stream]) + 1) // 2:]
                encoded = json.dumps(evidence, ensure_ascii=False, indent=2).encode("utf-8")
            if len(encoded) > 32768:
                evidence["argv"] = [safe_text(argument, 128) for argument in arguments[:32]]
                encoded = json.dumps(evidence, ensure_ascii=False, indent=2).encode("utf-8")
            directory = self.root / ".local"
            destination = directory / "native-error.json"
            temporary = directory / "native-error.tmp"
            with self._diagnostic_lock:
                if directory.is_symlink() or destination.is_symlink() or temporary.is_symlink():
                    return
                directory.mkdir(mode=0o700, exist_ok=True)
                temporary.write_bytes(encoded)
                temporary.chmod(0o600)
                os.replace(temporary, destination)
        except (OSError, ValueError):
            # Diagnostic storage must not replace the safe original failure.
            pass

    def _command(self, arguments, *, run_id=None, input_text=None, timeout=120, check=True):
        environment = dict(os.environ)
        environment["COMPOSE_PROJECT_NAME"] = "restorelab"
        environment["RESTORELAB_RUN_ID"] = run_id or "unconfigured"
        try:
            result = subprocess.run(
                arguments, cwd=self.root, env=environment, input=input_text,
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            self._record_native_error(arguments, run_id=run_id, stdout=error.stdout, stderr=error.stderr, timed_out=True, fatal=check)
            raise EngineError("native_timeout", "Operasi PostgreSQL melewati batas waktu. Bukti yang ada dipertahankan.") from error
        except OSError as error:
            self._record_native_error(arguments, run_id=run_id, stderr=type(error).__name__, fatal=check)
            raise EngineError("native_unavailable", "Docker atau alat PostgreSQL belum tersedia.") from error
        if result.returncode:
            self._record_native_error(arguments, run_id=run_id, stdout=result.stdout, stderr=result.stderr, returncode=result.returncode, fatal=check)
        if check and result.returncode:
            raise EngineError("native_command_failed", "Operasi native gagal. Periksa log lokal RestoreLab; kredensial tidak ditampilkan.")
        return result

    def _compose(self, *arguments, run_id=None, input_text=None, timeout=120, check=True):
        return self._command(
            ["docker", "compose", "--project-name", "restorelab", "--project-directory", str(self.root), "--file", str(self.root / "compose.yaml"), *arguments],
            run_id=run_id, input_text=input_text, timeout=timeout, check=check,
        )

    def _tools(self, script, *arguments, run_id=None, check=True):
        return self._compose("run", "--rm", "-T", "--no-deps", "tools", "/opt/restorelab/" + script, *arguments, run_id=run_id, check=check)

    def _sql(self, sql, *, service="primary", run_id=None, check=True):
        return self._compose(
            "exec", "-T", service, "psql", "-X", "-q", "-A", "-t", "-w", "-U", "restorelab", "-d", "restorelab", "-v", "ON_ERROR_STOP=1",
            input_text=sql, run_id=run_id, check=check, timeout=30,
        )

    def _json_sql(self, sql, *, service="primary", run_id=None, check=True):
        result = self._sql(sql, service=service, run_id=run_id, check=check)
        if result.returncode:
            return None
        try:
            return json.loads(result.stdout.strip())
        except (ValueError, TypeError) as error:
            raise EngineError("invalid_native_evidence", "Bukti PostgreSQL tidak mempunyai format yang diharapkan.") from error

    def _guard(self, run_id=None):
        run_check = "" if run_id is None else " AND active_run_id='%s'" % run_id
        return """DO $$ BEGIN
          IF current_database()<>'restorelab' OR current_user<>'restorelab'
             OR pg_is_in_recovery() OR current_setting('server_version_num')::int NOT BETWEEN 170000 AND 179999
             OR NOT EXISTS(SELECT 1 FROM restorelab.instance_guard WHERE singleton AND lab_id='restorelab-synthetic-v1'%s)
          THEN RAISE EXCEPTION 'Synthetic lab identity or active run mismatch'; END IF;
        END $$;""" % run_check

    def _assert_primary(self, run_id=None):
        self._sql(self._guard(run_id), run_id=run_id)
        result = self._json_sql("SELECT json_build_object('system_id',system_identifier::text) FROM pg_control_system();", run_id=run_id)
        if not isinstance(result, dict) or not re.fullmatch(r"[0-9]+", result.get("system_id", "")):
            raise EngineError("identity_mismatch", "Identitas cluster PostgreSQL tidak cocok dengan lab.")
        system_id = result["system_id"]
        marker = self._compose("exec", "-T", "primary", "/bin/sh", "-ec", 'test "$(cat "$PGDATA/.restorelab-primary")" = "restorelab-synthetic-v1:primary" && cat /archive/.restorelab-system-id', run_id=run_id)
        if marker.stdout.strip() != system_id:
            raise EngineError("identity_mismatch", "Marker cluster dan arsip WAL tidak cocok.")
        return system_id

    def _snapshot(self, run_id, *, service="primary"):
        rows = self._json_sql("""SELECT COALESCE(json_agg(json_build_object(
            'id',id,'run_id',run_id,'customer_code',customer_code,'amount_cents',amount_cents,'status',status
        ) ORDER BY id),'[]'::json) FROM restorelab.orders;""", service=service, run_id=run_id)
        if not isinstance(rows, list) or any(not isinstance(row, dict) or row.get("run_id") != run_id for row in rows):
            raise EngineError("run_marker_mismatch", "Data pesanan tidak cocok dengan marker latihan aktif.")
        fingerprint = hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        return {"row_count": len(rows), "fingerprint": fingerprint}, rows

    def health(self):
        try:
            result = self._compose("ps", "--format", "json", check=False, timeout=10)
            return {"available": result.returncode == 0, "project": "restorelab"}
        except EngineError:
            return {"available": False, "project": "restorelab"}

    def verify_backup(self, run_id):
        """Out-of-band native verification hook; the HTTP service does not expose it."""
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise EngineError("invalid_run_id", "ID latihan harus berisi 32 karakter heksadesimal kecil.")
        self._stage = "verify_backup"
        self._tools("verify-backup.sh", run_id, run_id=run_id)
        return {"manifest_verified": True}

    def execute(self, run_id, emit):
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise EngineError("invalid_run_id", "ID latihan harus berisi 32 karakter heksadesimal kecil.")
        if not _EXECUTION_LOCK.acquire(blocking=False):
            raise EngineError("drill_active", "Latihan native lain masih berjalan.")
        try:
            return self._execute(run_id, emit)
        finally:
            _EXECUTION_LOCK.release()

    def _execute(self, run_id, emit):
        started = time.monotonic()
        target_name = "restorelab_" + run_id

        def phase(step, message):
            self._stage = step
            emit(step, message)

        phase("prepare", "Menyiapkan 100 pesanan sintetis pada cluster lab.")
        active_tools = self._command([
            "docker", "ps", "--filter", "label=com.docker.compose.project=restorelab",
            "--filter", "label=com.docker.compose.service=tools", "--filter", "status=running", "--quiet",
        ], run_id=run_id, timeout=20)
        if active_tools.stdout.strip():
            raise EngineError("stale_native_operation", "Alat native dari proses sebelumnya masih berjalan. Periksa container RestoreLab sebelum memulai latihan baru.")
        self._command([sys.executable, str(self.root / "infrastructure" / "bootstrap.py")], run_id=run_id)
        self._compose("run", "--rm", "-T", "--no-deps", "--user", "0:0", "tools", "/opt/restorelab/init-volumes.sh", run_id=run_id)
        self._compose("up", "-d", "--wait", "primary", run_id=run_id)
        system_id = self._assert_primary()
        self._sql("BEGIN;" + self._guard() + """
          INSERT INTO restorelab.run_registry(run_id) VALUES('%s');
          UPDATE restorelab.instance_guard SET active_run_id='%s' WHERE singleton;
          TRUNCATE restorelab.orders,restorelab.incidents;
          INSERT INTO restorelab.orders(id,run_id,customer_code,amount_cents,status)
          SELECT id,'%s','CUSTOMER-'||lpad(id::text,3,'0'),id*137,'paid' FROM generate_series(1,100) AS id;
          COMMIT;""" % (run_id, run_id, run_id), run_id=run_id)
        base, _ = self._snapshot(run_id)
        if base["row_count"] != 100:
            raise EngineError("fixture_mismatch", "Jumlah awal pesanan sintetis tidak sesuai.")

        phase("backup", "Membuat physical base backup beserta manifest PostgreSQL.")
        self._assert_primary(run_id)
        self._tools("backup.sh", run_id, system_id, run_id=run_id)
        phase("verify_backup", "Memeriksa manifest dan WAL backup memakai pg_verifybackup.")
        self.verify_backup(run_id)

        phase("changes", "Menambahkan tiga transaksi setelah base backup.")
        self._assert_primary(run_id)
        self._sql("BEGIN;" + self._guard(run_id) + """
          INSERT INTO restorelab.orders(id,run_id,customer_code,amount_cents,status)
          SELECT id,'%s','CUSTOMER-'||lpad(id::text,3,'0'),id*137,'paid' FROM generate_series(101,103) AS id;
          COMMIT;""" % run_id, run_id=run_id)
        before, before_rows = self._snapshot(run_id)

        phase("restore_point", "Membuat named restore point sesudah transaksi baru.")
        self._assert_primary(run_id)
        point = self._json_sql("SELECT json_build_object('target_lsn',pg_create_restore_point('%s')::text);" % target_name, run_id=run_id)
        target_lsn = point.get("target_lsn", "") if isinstance(point, dict) else ""
        if not _LSN.fullmatch(target_lsn):
            raise EngineError("invalid_restore_point", "PostgreSQL tidak menghasilkan restore point yang valid.")
        segment = self._json_sql("SELECT to_json(pg_walfile_name('%s'::pg_lsn));" % target_lsn, run_id=run_id)
        if not isinstance(segment, str) or not _WAL.fullmatch(segment):
            raise EngineError("invalid_wal_segment", "Identitas segmen WAL target tidak valid.")

        phase("incident", "Mensimulasikan penghapusan pesanan 11 sampai 20 dan mencatat marker insiden.")
        self._assert_primary(run_id)
        self._sql("BEGIN;" + self._guard(run_id) + """
          DELETE FROM restorelab.orders WHERE id BETWEEN 11 AND 20 AND run_id='%s';
          INSERT INTO restorelab.incidents(run_id,incident_type) VALUES('%s','delete_orders');
          COMMIT;""" % (run_id, run_id), run_id=run_id)
        damaged, damaged_rows = self._snapshot(run_id)
        source_incident_count = self._json_sql("SELECT to_json(count(*)) FROM restorelab.incidents WHERE run_id='%s';" % run_id, run_id=run_id)
        incident_lsn = self._json_sql("SELECT to_json(pg_current_wal_lsn()::text);", run_id=run_id)
        if not isinstance(incident_lsn, str) or not _LSN.fullmatch(incident_lsn):
            raise EngineError("invalid_incident_lsn", "PostgreSQL tidak menghasilkan posisi WAL insiden yang valid.")

        phase("archive", "Mengarsipkan WAL yang memuat transaksi dan restore point.")
        self._assert_primary(run_id)
        self._sql(self._guard(run_id) + "SELECT pg_switch_wal();", run_id=run_id)
        deadline = time.monotonic() + 60
        while self._tools("check-archive.sh", run_id, segment, run_id=run_id, check=False).returncode:
            if time.monotonic() >= deadline:
                raise EngineError("archive_timeout", "Segmen WAL target belum tersedia pada arsip lab.")
            time.sleep(0.5)

        phase("recover", "Memulihkan clone terpisah dan berhenti pada named restore point.")
        recovery_started = time.monotonic()
        self._assert_primary(run_id)
        self._compose("--profile", "recovery", "stop", "recovery", run_id=run_id)
        self._tools("prepare-recovery.sh", run_id, target_name, run_id=run_id)
        self._compose("--profile", "recovery", "up", "-d", "--force-recreate", "recovery", run_id=run_id)
        deadline = time.monotonic() + 90
        recovery = None
        recovery_query = """SELECT json_build_object(
          'in_recovery',pg_is_in_recovery(),'paused',pg_is_wal_replay_paused(),
          'pause_state',pg_get_wal_replay_pause_state(),
          'replay_lsn',pg_last_wal_replay_lsn()::text,
          'target_name',current_setting('recovery_target_name'),
          'target_reached',COALESCE(pg_last_wal_replay_lsn()>='%s'::pg_lsn AND pg_last_wal_replay_lsn()<'%s'::pg_lsn,false),
          'system_identifier',(SELECT system_identifier::text FROM pg_control_system()),
          'run_marker',(SELECT active_run_id FROM restorelab.instance_guard WHERE lab_id='restorelab-synthetic-v1'),
          'incident_marker_count',(SELECT count(*) FROM restorelab.incidents WHERE run_id='%s')
        );""" % (target_lsn, incident_lsn, run_id)
        while time.monotonic() < deadline:
            recovery = self._json_sql(recovery_query, service="recovery", run_id=run_id, check=False)
            if isinstance(recovery, dict) and recovery.get("in_recovery") and recovery.get("paused") and recovery.get("pause_state") == "paused":
                break
            time.sleep(0.5)
        else:
            raise EngineError("recovery_timeout", "Clone belum berhenti pada restore point dalam batas waktu.")

        phase("verify", "Membandingkan fingerprint, transaksi baru, dan marker insiden pada clone.")
        restored, restored_rows = self._snapshot(run_id, service="recovery")
        self._assert_primary(run_id)
        source_now, _ = self._snapshot(run_id)
        recovered_ids = sorted({row["id"] for row in restored_rows} - {row["id"] for row in damaged_rows})
        post_backup = [row for row in restored_rows if row["id"] in (101, 102, 103)]
        expected_post = [row for row in before_rows if row["id"] in (101, 102, 103)]
        incident_excluded = recovery.get("incident_marker_count") == 0
        verified = (
            [base["row_count"], before["row_count"], damaged["row_count"], restored["row_count"]] == [100, 103, 93, 103]
            and before["fingerprint"] == restored["fingerprint"] and before_rows == restored_rows
            and source_now == damaged and recovered_ids == list(range(11, 21))
            and len(post_backup) == 3 and post_backup == expected_post and incident_excluded
            and source_incident_count == 1 and recovery.get("system_identifier") == system_id
            and recovery.get("target_name") == target_name and recovery.get("target_reached") is True
            and recovery.get("run_marker") == run_id
        )
        if not verified:
            raise EngineError("verification_failed", "Hasil clone tidak memenuhi seluruh pemeriksaan pemulihan. Bukti lab dipertahankan.")
        return {
            "run_id": run_id, "scenario": "delete_orders", "strategy": "physical_backup_wal_named_target", "verified": True,
            "backup": {"manifest_verified": True, "system_identifier": system_id},
            "snapshots": {"base": base, "before_incident": before, "after_incident": damaged, "restored": restored},
            "recovery": {
                "target_name": target_name, "target_lsn": target_lsn, "wal_segment": segment,
                "in_recovery": recovery["in_recovery"], "paused": recovery["paused"],
                "replay_lsn": recovery["replay_lsn"], "incident_marker_count": recovery["incident_marker_count"],
                "incident_lsn": incident_lsn, "run_marker": recovery["run_marker"],
                "source_incident_marker_count": source_incident_count, "system_identifier": recovery["system_identifier"],
            },
            "measurements": {"recovery_seconds": round(time.monotonic() - recovery_started, 3), "drill_seconds": round(time.monotonic() - started, 3)},
            "findings": {"recovered_ids": recovered_ids, "post_backup_rows_verified": True, "incident_excluded": True},
        }
