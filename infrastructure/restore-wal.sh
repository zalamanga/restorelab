#!/bin/sh
set -eu
wal_file="$1"
target_file="$2"
printf '%s\n' "$wal_file" | grep -Eq '^([0-9A-F]{24}|[0-9A-F]{8}\.history)$' || exit 90
[ "$(cat /archive/.restorelab-volume)" = 'restorelab-synthetic-v1:/archive' ] || exit 91
[ "$(cat "$PGDATA/../.restorelab-system-id")" = "$(cat /archive/.restorelab-system-id)" ] || exit 92
[ -f "/archive/$wal_file" ] && [ ! -L "/archive/$wal_file" ] || exit 93
cp "/archive/$wal_file" "$target_file"
