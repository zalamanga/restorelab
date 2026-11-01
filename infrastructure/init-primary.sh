#!/bin/sh
set -eu
[ "$(cat /archive/.restorelab-volume)" = 'restorelab-synthetic-v1:/archive' ] || exit 34
system_id="$(pg_controldata "$PGDATA" | sed -n 's/^Database system identifier: *//p')"
case "$system_id" in ''|*[!0-9]*) exit 35 ;; esac
if [ -e /archive/.restorelab-system-id ]; then
    [ "$(cat /archive/.restorelab-system-id)" = "$system_id" ] || exit 36
else
    printf '%s\n' "$system_id" > /archive/.restorelab-system-id
fi
printf '%s\n' 'restorelab-synthetic-v1:primary' > "$PGDATA/.restorelab-primary"
printf '%s\n' 'host replication restorelab all scram-sha-256' >> "$PGDATA/pg_hba.conf"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
DO $$ BEGIN
  IF current_database() <> 'restorelab' OR current_user <> 'restorelab' THEN
    RAISE EXCEPTION 'Wrong synthetic lab identity';
  END IF;
END $$;
CREATE SCHEMA restorelab;
CREATE TABLE restorelab.instance_guard (
    singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
    lab_id text NOT NULL CHECK(lab_id='restorelab-synthetic-v1'),
    active_run_id text CHECK(active_run_id ~ '^[0-9a-f]{32}$')
);
INSERT INTO restorelab.instance_guard(singleton,lab_id) VALUES(true,'restorelab-synthetic-v1');
CREATE TABLE restorelab.run_registry(run_id text PRIMARY KEY CHECK(run_id ~ '^[0-9a-f]{32}$'), started_at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE TABLE restorelab.orders (
    id integer PRIMARY KEY,
    run_id text NOT NULL CHECK(run_id ~ '^[0-9a-f]{32}$'),
    customer_code text NOT NULL,
    amount_cents bigint NOT NULL CHECK(amount_cents>0),
    status text NOT NULL CHECK(status='paid')
);
CREATE TABLE restorelab.incidents(run_id text NOT NULL, incident_type text NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp());
SQL
