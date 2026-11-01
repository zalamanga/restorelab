#!/bin/sh
set -eu
run_id="$1"
system_id="$2"
case "$run_id" in ''|*[!0-9a-f]*) exit 50 ;; esac
[ "${#run_id}" -eq 32 ] || exit 51
[ "$(cat /backups/.restorelab-volume)" = 'restorelab-synthetic-v1:/backups' ] || exit 52
[ "$(cat /archive/.restorelab-system-id)" = "$system_id" ] || exit 53
export PGPASSWORD="$(cat /run/secrets/postgres_password)"
guard="$(psql -X -A -t -w -h primary -U restorelab -d restorelab -v ON_ERROR_STOP=1 -c "SELECT current_database()='restorelab' AND current_user='restorelab' AND NOT pg_is_in_recovery() AND EXISTS(SELECT 1 FROM restorelab.instance_guard WHERE lab_id='restorelab-synthetic-v1' AND active_run_id='$run_id');")"
[ "$guard" = 't' ] || exit 54
directory="/backups/$run_id"
[ ! -e "$directory" ] && [ ! -L "$directory" ] || exit 55
mkdir "$directory"
printf '%s\n' "$run_id" > "$directory/.restorelab-run"
printf '%s\n' "$system_id" > "$directory/.restorelab-system-id"
pg_basebackup -h primary -U restorelab -w -D "$directory/base" --format=plain --wal-method=stream --checkpoint=fast --manifest-checksums=SHA256
