import fs from "node:fs";
import { test, expect } from "@playwright/test";
import { fixtureRun, fixtureState, interceptState } from "./fixtures.js";

test("actual native recovery evidence renders and downloads unchanged", async ({ page }, testInfo) => {
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
    const response = await page.request.get("/api/state");
    expect(response.ok()).toBe(true);
    const state = await response.json();
    const native = state.runs.find((run) => run.status === "verified" && run.report?.verified === true);
    expect(native, "Run tests/native_verify.py against real PostgreSQL before this browser suite").toBeTruthy();
    await page.goto("/");
    await page.getByRole("button", { name: new RegExp(`^Latihan ${native.id},`) }).click();
    await expect(page.locator("#run-status")).toHaveText("Terverifikasi");
    await expect(page.locator(".snapshot-number")).toHaveText(["100", "103", "93", "103"]);
    await expect(page.locator("#timeline .phase")).toHaveCount(9);
    await page.locator("#technical-details summary").click();
    await expect(page.locator("#technical-evidence")).toContainText(native.report.recovery.target_name);
    await expect(page.locator("#fingerprints")).toContainText(native.report.snapshots.restored.fingerprint);
    const [download] = await Promise.all([
        page.waitForEvent("download"),
        page.getByRole("link", { name: "Unduh laporan JSON", exact: true }).click(),
    ]);
    expect(download.suggestedFilename()).toBe(`restorelab-${native.id}.json`);
    const saved = testInfo.outputPath("native-report.json");
    await download.saveAs(saved);
    expect(JSON.parse(fs.readFileSync(saved, "utf8"))).toEqual(native.report);
    await page.evaluate(async () => { await document.fonts.ready; });
    await page.screenshot({ path: testInfo.outputPath("native-verified.png"), fullPage: true, animations: "disabled" });
    expect(errors).toEqual([]);
});

test("UI fixture: unknown POST outcome reuses its identity after reconnect", async ({ page }) => {
    let state = fixtureState();
    await interceptState(page, () => state);
    const requests = [];
    await page.route("**/api/runs", async (route) => {
        requests.push(route.request().postDataJSON());
        if (requests.length === 1) return route.abort("failed");
        const run = fixtureRun("queued");
        run.request_id = requests[1].request_id;
        state = fixtureState([run]);
        await route.fulfill({ status: 202, json: run });
    });
    await page.goto("/");
    await expect(page.locator("#run-empty")).toContainText("Belum ada latihan");
    await expect(page.locator("#start-button")).toBeDisabled();
    await page.getByRole("checkbox").check();
    await page.getByRole("button", { name: "Jalankan latihan pemulihan", exact: true }).click();
    await expect(page.locator("#network-error")).toBeVisible();
    await expect(page.locator("#start-button")).toBeDisabled();
    await page.getByRole("button", { name: "Coba hubungkan lagi", exact: true }).click();
    await expect(page.locator("#network-error")).toBeHidden();
    await page.getByRole("button", { name: "Kirim ulang permintaan yang sama", exact: true }).click();
    await expect(page.locator("#run-status")).toHaveText("Dalam antrean");
    expect(requests).toHaveLength(2);
    expect(requests[0]).toEqual(requests[1]);
    expect(requests[0]).toEqual({ request_id: expect.stringMatching(/^[a-f0-9]{32}$/), scenario: "delete_orders", acknowledge: "SYNTHETIC_ONLY" });
    await expect(page.getByRole("checkbox")).not.toBeChecked();
    await expect(page.locator("#start-button")).toBeDisabled();
});

test("UI fixture: polling preserves focus and offline retry preserves evidence", async ({ page }) => {
    await page.clock.install();
    const run = fixtureRun();
    const state = fixtureState([run]);
    let offline = false;
    let reads = 0;
    await interceptState(page, () => state);
    await page.route("**/api/state", (route) => {
        reads += 1;
        return route.fulfill(offline ? { status: 503, json: { error: { code: "fixture_offline", message: "[UI TEST FIXTURE] Offline." } } } : { json: state });
    });
    await page.goto("/");
    await expect(page.locator("#run-status")).toHaveText("Terverifikasi");
    const history = page.locator("#history-list .history-button").first();
    await history.focus();
    const before = reads;
    await page.clock.fastForward(15001);
    await expect.poll(() => reads).toBeGreaterThan(before);
    await expect(history).toBeFocused();
    await page.locator("#technical-details summary").click();
    await page.locator("#technical-details summary").focus();
    const secondRead = reads;
    await page.clock.fastForward(15001);
    await expect.poll(() => reads).toBeGreaterThan(secondRead);
    await expect(page.locator("#technical-details")).toHaveAttribute("open", "");
    await expect(page.locator("#technical-details summary")).toBeFocused();
    offline = true;
    await page.getByRole("button", { name: "Perbarui status", exact: true }).click();
    await expect(page.locator("#network-error")).toBeVisible();
    await expect(page.locator(".snapshot-number")).toHaveText(["100", "103", "93", "103"]);
    await expect(page.locator("#download-report")).toBeVisible();
    offline = false;
    await page.getByRole("button", { name: "Coba hubungkan lagi", exact: true }).click();
    await expect(page.locator("#network-error")).toBeHidden();
    await expect(page.locator("#run-status")).toHaveText("Terverifikasi");
});

test("UI fixture: late history responses cannot overwrite the latest selection", async ({ page }) => {
    const first = fixtureRun("verified", "a");
    const delayed = fixtureRun("failed", "b");
    const latest = fixtureRun("interrupted", "c");
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    let delayedRequested = false;
    await interceptState(page, () => fixtureState([first, delayed, latest]), async (id) => {
        if (id === delayed.id) { delayedRequested = true; await gate; }
        return [first, delayed, latest].find((run) => run.id === id);
    });
    try {
        await page.goto("/");
        await expect(page.locator("#run-status")).toHaveText("Terverifikasi");
        await page.getByRole("button", { name: new RegExp(`^Latihan ${delayed.id},`) }).click();
        await expect.poll(() => delayedRequested).toBe(true);
        await page.getByRole("button", { name: new RegExp(`^Latihan ${latest.id},`) }).click();
        await expect(page.locator("#run-status")).toHaveText("Terputus");
        const lateResponse = page.waitForResponse((response) => new URL(response.url()).pathname === `/api/runs/${delayed.id}`);
        release();
        await (await lateResponse).finished();
        await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        await expect(page.locator("#run-meta")).toContainText(latest.id);
        await expect(page.locator("#run-status")).toHaveText("Terputus");
        await expect(page.locator("#download-report")).toBeHidden();
    } finally {
        release();
    }
});

test("UI fixture: report and event strings remain text, including long identifiers", async ({ page }) => {
    const run = fixtureRun();
    const hostile = '<img src="/fixture-xss" onerror="window.fixtureExecuted=true">';
    run.report.recovery.target_name = hostile + "LONG".repeat(100);
    run.events[8].message = "[UI TEST FIXTURE] " + hostile;
    run.events[8].details = { fixture: hostile };
    await interceptState(page, () => fixtureState([run]));
    await page.goto("/");
    await expect(page.locator("#run-status")).toHaveText("Terverifikasi");
    await page.locator("#technical-details summary").click();
    await expect(page.locator("#technical-evidence")).toContainText(hostile);
    await page.locator("#event-details > summary").click();
    await expect(page.locator("#event-log")).toContainText(hostile);
    await expect(page.locator('img[src="/fixture-xss"]')).toHaveCount(0);
    expect(await page.evaluate(() => Boolean(window.fixtureExecuted))).toBe(false);
    await page.setViewportSize({ width: 320, height: 960 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);
});

test("UI fixture: quota, active and terminal states remain usable across five widths", async ({ page }, testInfo) => {
    let state = fixtureState();
    await interceptState(page, () => state);
    await page.emulateMedia({ reducedMotion: "reduce" });
    const observations = [];
    for (const status of ["empty", "running", "interrupted", "failed", "verified"]) {
        state = fixtureState(status === "empty" ? [] : [fixtureRun(status)]);
        for (const width of [320, 390, 768, 1024, 1440]) {
            await page.setViewportSize({ width, height: 960 });
            await page.goto("/");
            await expect(page.locator("#connection-status")).toContainText("Terhubung");
            const measurement = await page.evaluate(() => ({
                documentWidth: document.documentElement.scrollWidth,
                reducedMotion: matchMedia("(prefers-reduced-motion: reduce)").matches,
                refreshHeight: document.querySelector("#refresh-button").getBoundingClientRect().height,
            }));
            expect(measurement.documentWidth).toBeLessThanOrEqual(width);
            expect(measurement.reducedMotion).toBe(true);
            expect(measurement.refreshHeight).toBeGreaterThanOrEqual(44);
            if (status === "running") await expect(page.getByRole("checkbox")).toBeDisabled();
            if (["failed", "interrupted"].includes(status)) await expect(page.locator("#download-report")).toBeHidden();
            observations.push({ status, width, ...measurement });
        }
    }
    state = fixtureState([fixtureRun()], { used_runs: 5 });
    await page.goto("/");
    await expect(page.locator("#quota")).toContainText("5 dari 5");
    await expect(page.getByRole("checkbox")).toBeDisabled();
    await expect(page.locator("#start-button")).toBeDisabled();
    await expect(page.locator("#download-report")).toBeVisible();
    await testInfo.attach("fixture-layout-measurements", { body: JSON.stringify(observations, null, 2), contentType: "application/json" });
});

test("UI fixture: keyboard acknowledgement enables only the guarded start action", async ({ page }) => {
    await interceptState(page, () => fixtureState());
    await page.goto("/");
    await expect(page.locator("#connection-status")).toContainText("Terhubung");
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "Lewati ke area kerja", exact: true })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "RestoreLab, halaman utama", exact: true })).toBeFocused();
    await page.getByRole("checkbox").focus();
    await page.keyboard.press("Space");
    await expect(page.getByRole("checkbox")).toBeChecked();
    await page.keyboard.press("Tab");
    await expect(page.locator("#start-button")).toBeFocused();
    await expect(page.locator("#start-button")).toBeEnabled();
    const focus = await page.locator("#start-button").evaluate((button) => ({
        style: getComputedStyle(button).outlineStyle, width: parseFloat(getComputedStyle(button).outlineWidth),
    }));
    expect(focus.style).not.toBe("none");
    expect(focus.width).toBeGreaterThanOrEqual(2);
});

test("actual UI acknowledgement starts and verifies a native PostgreSQL recovery", async ({ page }, testInfo) => {
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
    const state = await (await page.request.get("/api/state")).json();
    expect(state.lab.active_run_id).toBeNull();
    expect(state.lab.used_runs).toBeLessThan(state.lab.max_runs);
    await page.goto("/");
    await expect(page.locator("#connection-status")).toContainText("Terhubung");
    await expect(page.locator("#start-button")).toBeDisabled();
    await page.getByRole("checkbox").check();
    const [response] = await Promise.all([
        page.waitForResponse((candidate) => new URL(candidate.url()).pathname === "/api/runs" && candidate.request().method() === "POST"),
        page.getByRole("button", { name: "Jalankan latihan pemulihan", exact: true }).click(),
    ]);
    expect(response.status()).toBe(202);
    const payload = response.request().postDataJSON();
    expect(payload).toEqual({ request_id: expect.stringMatching(/^[0-9a-f]{32}$/), scenario: "delete_orders", acknowledge: "SYNTHETIC_ONLY" });
    const accepted = await response.json();
    expect(accepted.id).toMatch(/^[0-9a-f]{32}$/);
    let observedRunning;
    await expect.poll(async () => {
        observedRunning = await (await page.request.get(`/api/runs/${accepted.id}`)).json();
        return observedRunning.status === "running" && ["prepare", "backup"].includes(observedRunning.step);
    }, { timeout: 15000, intervals: [100, 200, 500] }).toBe(true);
    await page.getByRole("button", { name: "Perbarui status", exact: true }).click();
    await expect(page.locator("#run-status")).toHaveText("Berjalan");
    await expect(page.locator('#timeline .phase[aria-current="step"] h3')).toHaveText(/^(Siapkan data sintetis|Buat backup fisik)$/);
    await expect(page.getByRole("checkbox")).toBeDisabled();
    fs.mkdirSync("docs/screenshots", { recursive: true });
    await page.evaluate(async () => { await document.fonts.ready; });
    await page.screenshot({ path: "docs/screenshots/01-running.png", fullPage: false, animations: "disabled" });
    fs.copyFileSync("docs/screenshots/01-running.png", testInfo.outputPath("native-running.png"));
    await testInfo.attach("native-running-phase", { body: JSON.stringify({ id: accepted.id, status: observedRunning.status, step: observedRunning.step }), contentType: "application/json" });
    await expect(page.locator("#run-status")).toHaveText("Terverifikasi", { timeout: 180000 });
    await expect(page.locator(".snapshot-number")).toHaveText(["100", "103", "93", "103"]);
    const completed = await (await page.request.get(`/api/runs/${accepted.id}`)).json();
    expect(completed.status).toBe("verified");
    expect(completed.report.run_id).toBe(accepted.id);
    expect(completed.report.snapshots.before_incident.fingerprint).toBe(completed.report.snapshots.restored.fingerprint);
    expect(completed.report.recovery.incident_marker_count).toBe(0);
    const [download] = await Promise.all([
        page.waitForEvent("download"), page.getByRole("link", { name: "Unduh laporan JSON", exact: true }).click(),
    ]);
    const destination = testInfo.outputPath("native-ui-report.json");
    await download.saveAs(destination);
    expect(JSON.parse(fs.readFileSync(destination, "utf8"))).toEqual(completed.report);
    expect(errors).toEqual([]);
});
