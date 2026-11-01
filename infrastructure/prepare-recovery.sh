#!/bin/sh
set -eu
run_id="$1"
target_name="$2"
case "$run_id" in ''|*[!0-9a-f]*) exit 80 ;; esac
[ "${#run_id}" -eq 32 ] || exit 81
[ "$target_name" = "restorelab_$run_id" ] || exit 82
[ "$(cat /recovery/.restorelab-volume)" = 'restorelab-synthetic-v1:/recovery' ] || exit 83
[ "$(cat "/backups/$run_id/.restorelab-run")" = "$run_id" ] || exit 84
directory="/recovery/$run_id"
[ ! -e "$directory" ] && [ ! -L "$directory" ] || exit 85
mkdir "$directory"
printf '%s\n' "$run_id" > "$directory/.restorelab-run"
cp "/backups/$run_id/.restorelab-system-id" "$directory/.restorelab-system-id"
mkdir "$directory/data"
cp -a "/backups/$run_id/base/." "$directory/data/"
[ "$(cat "$directory/data/PG_VERSION")" = '17' ] || exit 86
cat >> "$directory/data/postgresql.auto.conf" <<EOF
restore_command = '/bin/sh /opt/restorelab/restore-wal.sh %f %p'
recovery_target_name = '$target_name'
recovery_target_action = 'pause'
recovery_target_timeline = 'current'
primary_conninfo = ''
EOF
touch "$directory/data/recovery.signal"
chmod 700 "$directory/data"
