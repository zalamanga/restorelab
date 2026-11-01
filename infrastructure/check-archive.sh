#!/bin/sh
set -eu
run_id="$1"
wal_file="$2"
case "$run_id" in ''|*[!0-9a-f]*) exit 70 ;; esac
[ "${#run_id}" -eq 32 ] || exit 71
case "$wal_file" in ''|*[!0-9A-F]*) exit 72 ;; esac
[ "${#wal_file}" -eq 24 ] || exit 73
[ "$(cat /archive/.restorelab-volume)" = 'restorelab-synthetic-v1:/archive' ] || exit 74
[ "$(cat "/backups/$run_id/.restorelab-system-id")" = "$(cat /archive/.restorelab-system-id)" ] || exit 75
[ -s "/archive/$wal_file" ] && [ ! -L "/archive/$wal_file" ]
