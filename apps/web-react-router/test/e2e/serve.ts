/**
 * Starts one of the browser test's servers with the clean environment from
 * setup.ts: `node test/e2e/serve.ts api` (migrations, then the API) or
 * `node test/e2e/serve.ts web` (the built web app, `node server.js`).
 * Playwright's webServer runs both and stops them after the run.
 */
import { type ChildProcess, spawn, spawnSync } from "node:child_process";

import { API_PORT, PYTHON, REPO_ROOT, WEB_DIR, apiEnvironment, webEnvironment } from "./setup.ts";

function run(): ChildProcess {
  const which = process.argv[2];
  if (which === "api") {
    const env = apiEnvironment();
    const migrated = spawnSync(PYTHON, ["-m", "alembic", "upgrade", "head"], { cwd: REPO_ROOT, env, stdio: "inherit" });
    if (migrated.status !== 0) process.exit(migrated.status ?? 1);
    return spawn(
      PYTHON,
      ["-m", "uvicorn", "api.index:app", "--host", "127.0.0.1", "--port", String(API_PORT), "--log-level", "warning"],
      { cwd: REPO_ROOT, env, stdio: "inherit" },
    );
  }
  if (which === "web") {
    return spawn(process.execPath, ["server.js"], { cwd: WEB_DIR, env: webEnvironment(), stdio: "inherit" });
  }
  console.error("Usage: node test/e2e/serve.ts api|web");
  process.exit(2);
}

const child = run();
for (const signal of ["SIGINT", "SIGTERM"] as const) process.on(signal, () => child.kill(signal));
child.on("exit", (code, signal) => process.exit(code ?? (signal ? 1 : 0)));
