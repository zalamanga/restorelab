export const phases = ["prepare", "backup", "verify_backup", "changes", "restore_point", "incident", "archive", "recover", "verify"];

export function fixtureRun(status = "verified", digit = "a") {
    const id = digit.repeat(32);
    const time = "2026-11-01T10:00:00.000Z";
    const report = {
        run_id: id, scenario: "delete_orders", strategy: "physical_backup_wal_named_target", verified: true,
        backup: { manifest_verified: true },
        snapshots: {
            base: { row_count: 100, fingerprint: "1".repeat(64) },
            before_incident: { row_count: 103, fingerprint: "2".repeat(64) },
            after_incident: { row_count: 93, fingerprint: "3".repeat(64) },
            restored: { row_count: 103, fingerprint: "2".repeat(64) },
        },
        recovery: { target_name: "UI_TEST_FIXTURE_" + id, target_lsn: "0/1600100", replay_lsn: "0/1600100",
            wal_segment: "000000010000000000000001", in_recovery: true, paused: true, incident_marker_count: 0 },
        findings: { recovered_ids: [11, 12, 13, 14, 15, 16, 17, 18, 19, 20], post_backup_rows_verified: true, incident_excluded: true },
        measurements: { recovery_seconds: 1.25, drill_seconds: 2.5 },
    };
    return {
        id, request_id: digit === "a" ? "b".repeat(32) : "a".repeat(32), status,
        step: status === "running" ? "recover" : "verify", created_at: time,
        started_at: status === "queued" ? null : time,
        finished_at: ["queued", "running"].includes(status) ? null : time,
        message: "[UI TEST FIXTURE] Synthetic browser-only state.",
        error: status === "failed" ? { code: "fixture_failure", message: "[UI TEST FIXTURE] Native recovery failed." } : null,
        events: (status === "queued" ? [] : phases.slice(0, status === "running" ? 8 : 9)).map((step, index) => ({
            sequence: index + 1, step, message: `[UI TEST FIXTURE] Phase ${step}`, at: time, details: { fixture: true },
        })),
        report: status === "verified" ? report : null,
    };
}

export function fixtureState(runs = [], overrides = {}) {
    const active = runs.find((run) => ["queued", "running"].includes(run.status)) || null;
    return {
        lab: { name: "RestoreLab", scope: "synthetic_only", max_runs: 5, used_runs: runs.length,
            active_run_id: active?.id || null, ...overrides },
        runs, active_run: active,
    };
}

export async function interceptState(page, getState, getDetail = null) {
    await page.route("**/api/state", (route) => route.fulfill({ json: getState() }));
    await page.route(/\/api\/runs\/[a-f0-9]{32}$/, async (route) => {
        const id = new URL(route.request().url()).pathname.split("/").at(-1);
        const run = getDetail ? await getDetail(id) : getState().runs.find((item) => item.id === id);
        await route.fulfill(run ? { json: run } : { status: 404, json: { error: { code: "not_found", message: "[UI TEST FIXTURE] Missing run." } } });
    });
    await page.route("**/api/runs", (route) => route.fulfill({
        status: 409, json: { error: { code: "fixture_guard", message: "[UI TEST FIXTURE] Native starts are disabled in this fixture." } },
    }));
}
