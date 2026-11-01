#!/bin/sh
set -eu
run_id="$1"
case "$run_id" in ''|*[!0-9a-f]*) exit 60 ;; esac
[ "${#run_id}" -eq 32 ] || exit 61
[ "$(cat /backups/.restorelab-volume)" = 'restorelab-synthetic-v1:/backups' ] || exit 62
[ "$(cat "/backups/$run_id/.restorelab-run")" = "$run_id" ] || exit 63
pg_verifybackup --exit-on-error "/backups/$run_id/base"
