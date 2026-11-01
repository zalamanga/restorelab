import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from restorelab.engine import Engine, EngineError


COPY_AND_CORRUPT = r"""set -eu
source_id="$1"
negative_id="$2"
source="/backups/$source_id"
destination="/backups/$negative_id"
test -f /backups/.restorelab-volume
test ! -L /backups/.restorelab-volume
test "$(cat /backups/.restorelab-volume)" = "restorelab-synthetic-v1:/backups"
test -d "$source" && test ! -L "$source"
test -f "$source/.restorelab-run" && test ! -L "$source/.restorelab-run"
test "$(cat "$source/.restorelab-run")" = "$source_id"
test -f "$source/.restorelab-system-id" && test ! -L "$source/.restorelab-system-id"
test -d "$source/base" && test ! -L "$source/base"
test ! -e "$destination" && test ! -L "$destination"
mkdir -m 700 "$destination"
printf '%s\n' "$negative_id" > "$destination/.restorelab-run"
cp -a "$source/.restorelab-system-id" "$destination/.restorelab-system-id"
cp -a "$source/base" "$destination/base"
test -f "$destination/base/PG_VERSION" && test ! -L "$destination/base/PG_VERSION"
printf '\nRESTORELAB_CORRUPTION_TEST\n' >> "$destination/base/PG_VERSION"
chown -R postgres:postgres "$destination"
"""


def confirm_native_rejection(root, negative_id, started_at):
    diagnostic_path = root / ".local" / "native-error.json"
    if diagnostic_path.parent.is_symlink() or diagnostic_path.is_symlink() or not diagnostic_path.is_file():
        raise AssertionError("The native rejection diagnostic is unavailable")
    with diagnostic_path.open("rb") as stream:
        raw = stream.read(32769)
    if len(raw) > 32768:
        raise AssertionError("The native rejection diagnostic exceeded its evidence bound")
    try:
        diagnostic = json.loads(raw.decode("utf-8"))
        if not isinstance(diagnostic, dict) or not isinstance(diagnostic.get("at"), str):
            raise ValueError("Invalid diagnostic timestamp")
        recorded_at = datetime.fromisoformat(diagnostic["at"].replace("Z", "+00:00"))
        argv = diagnostic["argv"]
        stderr = diagnostic["stderr"]
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise AssertionError("The native rejection diagnostic has an invalid structure") from None
    if recorded_at.tzinfo is None or recorded_at < started_at:
        raise AssertionError("The native rejection diagnostic predates this verification")
    if diagnostic.get("run_id") != negative_id or diagnostic.get("stage") != "verify_backup":
        raise AssertionError("The native rejection diagnostic belongs to a different operation")
    if diagnostic.get("fatal") is not True or diagnostic.get("timed_out") is not False or type(diagnostic.get("returncode")) is not int or diagnostic["returncode"] != 1:
        raise AssertionError("The native verifier did not record the expected fatal integrity rejection")
    if not isinstance(argv, list) or "/opt/restorelab/verify-backup.sh" not in argv or negative_id not in argv:
        raise AssertionError("The native rejection was not produced by the guarded backup verifier")
    if not isinstance(stderr, str) or "pg_verifybackup" not in stderr.lower() or "PG_VERSION" not in stderr:
        raise AssertionError("The native rejection does not identify pg_verifybackup and the corrupted file")
    if re.search(r"\b(?:checksum|size|length)\b", stderr, re.IGNORECASE) is None:
        raise AssertionError("The native rejection did not identify a manifest integrity mismatch")
    return True


def verify_corruption(root, source_run_id, output):
    if not isinstance(source_run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", source_run_id):
        raise ValueError("A verified source run UUID32 is required")
    engine = Engine(root)
    if engine.verify_backup(source_run_id) != {"manifest_verified": True}:
        raise AssertionError("The original successful backup did not verify")
    negative_id = uuid.uuid4().hex
    if negative_id == source_run_id:
        raise AssertionError("The corruption clone must have a distinct identity")
    engine._compose(
        "run", "--rm", "-T", "--no-deps", "--user", "0:0", "tools", "-c", COPY_AND_CORRUPT,
        "restorelab-corruption", source_run_id, negative_id, run_id=negative_id,
    )
    verification_started = datetime.now(timezone.utc)
    try:
        engine.verify_backup(negative_id)
    except EngineError as error:
        if error.code != "native_command_failed":
            raise AssertionError("The corruption clone failed for an unexpected reason") from None
        native_rejection_confirmed = confirm_native_rejection(root, negative_id, verification_started)
    else:
        raise AssertionError("pg_verifybackup accepted the deliberately corrupted cloned PG_VERSION file")
    if engine.verify_backup(source_run_id) != {"manifest_verified": True}:
        raise AssertionError("The original successful backup changed during the negative check")
    evidence = {
        "status": "PASS", "source_run_id": source_run_id, "negative_run_id": negative_id,
        "native_tool": "pg_verifybackup", "corrupted_clone_file": "base/PG_VERSION",
        "negative_rejected": True, "error_code": "native_command_failed",
        "native_rejection_confirmed": native_rejection_confirmed,
        "original_manifest_verified_before_and_after": True,
        "original_backup_mutated": False, "source_database_mutated": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence))


def main():
    parser = argparse.ArgumentParser(description="Reject a corrupted isolated clone of a verified native backup.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--source-run-id")
    parser.add_argument("--native-evidence", type=Path, default=Path("test-results/native-evidence.json"))
    parser.add_argument("--output", type=Path, default=Path("test-results/native-corruption.json"))
    arguments = parser.parse_args()
    try:
        source = arguments.source_run_id
        if source is None:
            native = json.loads(arguments.native_evidence.read_text(encoding="utf-8"))
            if native.get("status") != "PASS" or native.get("run", {}).get("status") != "verified":
                raise ValueError("A passing native job evidence file is required")
            source = native["run"]["id"]
        verify_corruption(arguments.root.resolve(), source, arguments.output)
    except (AssertionError, OSError, ValueError, EngineError) as error:
        parser.exit(1, f"Native corruption verification failed: {error}\n")


if __name__ == "__main__":
    main()
