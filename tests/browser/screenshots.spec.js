import fs from "node:fs";
import { test, expect } from "@playwright/test";

test("capture actual verified native evidence without starting another drill", async ({ page }) => {
    const response = await page.request.get("/api/state");
    expect(response.ok()).toBe(true);
    const state = await response.json();
    const native = state.runs.find((run) => run.status === "verified" && run.report?.verified === true);
    expect(native, "A real verified PostgreSQL drill is required for public screenshots").toBeTruthy();
    fs.mkdirSync("docs/screenshots", { recursive: true });
    const capture = async (name, { fullPage = true, target = null } = {}) => {
        await expect(page.locator("#run-status")).toHaveText("Terverifikasi");
        await expect(page.locator("#run-loading")).toBeHidden();
        await expect(page.locator("#network-error")).toBeHidden();
        await page.evaluate(async () => { await document.fonts.ready; });
        const path = `docs/screenshots/${name}.png`;
        if (target) await target.screenshot({ path, animations: "disabled" });
        else await page.screenshot({ path, fullPage, animations: "disabled" });
    };
    await page.goto("/");
    await page.getByRole("button", { name: new RegExp(`^Latihan ${native.id},`) }).click();
    await expect(page.locator(".snapshot-number")).toHaveText(["100", "103", "93", "103"]);
    await capture("02-verified");
    await page.locator("#technical-details summary").click();
    await expect(page.locator("#technical-evidence")).toContainText(native.report.recovery.target_name);
    await capture("03-technical-evidence", { target: page.locator("#report-panel") });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
    await capture("04-mobile", { fullPage: false });
});
