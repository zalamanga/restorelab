# RestoreLab implementation contract

Repository: restorelab. Project name: RestoreLab. Runtime folder initially Belum Push, canonical after publication done push repo/restorelab. All task data on D:. Default HTTP 127.0.0.1:8140. PostgreSQL 17 cached pinned image same digest used by RepairDesk; no PostgreSQL host ports. Docker Compose project is fixed restorelab, isolated volumes/marker. Never touch RepairDesk or other projects.

## Native engine owned by engine agent

Files: compose.yaml, infrastructure/**, restorelab/engine.py only. Python stdlib host controller; Docker postgres image provides psql/pg_basebackup/pg_verifybackup. No third-party Python dependency required.

Public interface:

```python
class EngineError(Exception):
    code: str
    # Constructor EngineError(code, message); str is safe for UI, no raw secrets/stderr.

class Engine:
    def __init__(self, root): ...
    def execute(self, run_id, emit): ...  # returns JSON-serializable report
    def health(self): ...  # cheap Docker/lab status dict; optional if slow
```

`run_id` is exactly 32 lowercase hex. `emit(step, message, details=None)` uses steps prepare, backup, verify_backup, changes, restore_point, incident, archive, recover, verify. Callback can raise InterruptedError on requested stop; native engine must not continue to next mutation. Each phase emits before work and may emit completion details. Engine validates fixed database identity and active drill marker before SQL writes; no arbitrary DB URL, SQL, volume, host path or uploaded backup accepted through HTTP.

Drill: prepare 100 synthetic orders -> physical plain base backup + manifest -> pg_verifybackup -> add orders101..103 after backup -> snapshot before incident -> named restore point -> delete exactly10 orders11..20 and record incident marker -> switch/archive WAL and verify required segment exists -> restore into a separate recovery cluster -> pause at named target -> compare restored orders with before-incident snapshot. Base100, before103, damaged93, restored103. Prove 3 post-backup orders recovered and incident marker excluded. Verification uses sorted canonical data fingerprint, run marker, native paused/in-recovery state and target identity. pg_verifybackup alone cannot mark success.

Keep archive on a dedicated named volume, backups/clones under per-run UUID directories. Never clear a nonempty arbitrary target; no auto-prune or global Docker cleanup. Controller default quota5 runs per persistent instance bounds lab disk growth; CLI can configure it. Keep source primary damaged as evidence. Old recovery clusters stopped before changing the active clone. At most one active native drill. No automatic retry after controller interruption; startup marks old running jobs interrupted. Recovery measurements use monotonic time and are called measured restore time, not a production RTO/RPO guarantee.

Required report fields:

```json
{"run_id":"32hex","scenario":"delete_orders","strategy":"physical_backup_wal_named_target","verified":true,
"backup":{"manifest_verified":true},
"snapshots":{"base":{"row_count":100,"fingerprint":"64hex"},"before_incident":{"row_count":103,"fingerprint":"64hex"},"after_incident":{"row_count":93,"fingerprint":"64hex"},"restored":{"row_count":103,"fingerprint":"64hex"}},
"recovery":{"target_name":"...","target_lsn":"...","wal_segment":"...","in_recovery":true,"paused":true,"replay_lsn":"...","incident_marker_count":0},
"measurements":{"recovery_seconds":0.0,"drill_seconds":0.0},
"findings":{"recovered_ids":[11,12,13,14,15,16,17,18,19,20],"post_backup_rows_verified":true,"incident_excluded":true}}
```

Additional bounded evidence fields allowed. Credentials generated locally .secrets, runtime config .runtime, ignored. Operations include ensure-primary/start, setup scripts, tools for backup/restore. Engine agent must document exact startup invocation for root scripts. Avoid healthcheck on one-shot tools. Root is sole Docker mutator/testing coordinator.

## HTTP/controller owned by root

Python package restorelab. SQLite persistent ledger under .data/controller.sqlite; cross-platform exclusive owner file lock. Single worker, no silent rerun of interrupted drill. On startup BOTH old queued/running attempts become interrupted; starting the workstation performs no native drill until a new explicit action. Immutable terminal evidence. Server binds loopback, Host/Origin checks on mutation, strict JSON, fixed scenario, bounded bodies, same-origin asset allowlist. Native runtime uses real engine; fake engine only tests. Max5 total attempts default, persists across restart. POST dedupe request_id UUID32 maps to original run; one active queued/running run; duplicate request returns existing run. GET never mutates.

Routes/envelopes:

```
GET /api/state -> {lab:{name:'RestoreLab',scope:'synthetic_only',max_runs:5,used_runs:N,active_run_id:null|string},runs:[run_summary...],active_run:run_detail|null}
GET /api/runs/<32hex> -> run_detail
GET /api/runs/<32hex>/report -> application/json attachment, only verified complete report
POST /api/runs body {request_id:'32hex',scenario:'delete_orders',acknowledge:'SYNTHETIC_ONLY'} ->202 run_detail (200 deduplicated)
GET /health -> {status:'ok'} controller health only
Errors HTTP400/403/404/409/413/415/503 -> {error:{code:'...',message:'Indonesian text'}}
```

Run summary/detail:

```json
{"id":"32hex","request_id":"32hex","status":"queued|running|verified|failed|interrupted","step":null,"created_at":"ISO UTC","started_at":null,"finished_at":null,"message":"...","error":null,"events":[{"sequence":1,"step":"prepare","message":"...","at":"ISO UTC","details":{}}],"report":null}
```

State and report JSON never contain credentials or raw Docker stderr. Polling GET /api/state every~2s while active, slower idle; do not update focused controls or announce unchanged status repeatedly. Reports rendered with textContent. Download real attachment link after verified only. UI can select history run via GET; no arbitrary SQL/reset/upload endpoints.

## UI owned by dashboard agent

Files www/index.html, www/styles.css, www/app.js only. Follow DESIGN.md and session antislop During. Indonesian working UI, semantic accessible native HTML. No fake sample success in default app. Empty state explains synthetic drill; checkbox/dialog acknowledgement for starting resets synthetic fixture; actual run timeline, row comparison100/103/93/103 when report present, technical evidence disclosure, download report, history, local quota. Network failure preserves last known data and gives retry. HTTP409 active/quota errors have readable status. No public logo/asset invention. Root captures actual PostgreSQL screens.

## Quality agent

Own tests/** and .github/**; independent unit tests for root controller/ledger + native integration/Playwright plan. Root will provide store/server interfaces shortly. Test strict perimeter, idempotency concurrency, one-active race, restart interrupted handling, quota persistence, verified report gating, validation and rendering. No runtime mutations until root coordinates. CI must run native drill before UI screenshot evidence. Reports/artifacts allowlist; no private data/secrets/traces/video upload.
