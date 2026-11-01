import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(import.meta.url);
const cli = path.join(path.dirname(require.resolve("@playwright/test/package.json")), "cli.js");
const result = spawnSync(process.execPath, [cli, "test", ...process.argv.slice(2)], {
    stdio: "inherit", env: { ...process.env, RESTORELAB_CAPTURE: "1" },
});
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
