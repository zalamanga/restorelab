#!/bin/sh
set -eu
run_id="$(basename "$(dirname "$PGDATA")")"
case "$run_id" in ''|*[!0-9a-f]*) exit 100 ;; esac
[ "${#run_id}" -eq 32 ] || exit 101
[ "$PGDATA" = "/recovery/$run_id/data" ] || exit 102
[ "$(cat /recovery/.restorelab-volume)" = 'restorelab-synthetic-v1:/recovery' ] || exit 103
[ "$(cat "/recovery/$run_id/.restorelab-run")" = "$run_id" ] || exit 104
[ -f "$PGDATA/recovery.signal" ] && [ "$(cat "$PGDATA/PG_VERSION")" = '17' ] || exit 105
exec docker-entrypoint.sh postgres -c archive_mode=off -c listen_addresses= -c shared_buffers=64MB -c max_connections=20
