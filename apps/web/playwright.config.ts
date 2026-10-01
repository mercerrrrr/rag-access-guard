import { randomBytes } from "node:crypto";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { defineConfig } from "@playwright/test";

process.env["RAG_E2E_PASSWORD"] ??= randomBytes(24).toString("hex");
process.env["RAG_ACCESS_GUARD_AUTH_LIMIT_SECRET"] ??= randomBytes(32).toString("hex");

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: true,
  timeout: 60000,
  reporter: "list",
  outputDir: process.env["RAG_E2E_OUTPUT_DIR"] ?? join(tmpdir(), "rag-access-guard-e2e-results"),
  use: {
    baseURL: "http://127.0.0.1:54174",
    viewport: { width: 1600, height: 900 },
    locale: "ru-RU",
    trace: "off",
    screenshot: "off",
    video: "off",
  },
  webServer: {
    command: "uv run --frozen python -m tests.e2e.server",
    cwd: "../..",
    url: "http://127.0.0.1:54174/api/health/ready",
    reuseExistingServer: false,
    timeout: 120000,
    gracefulShutdown: { signal: "SIGTERM", timeout: 10000 },
    stdout: "ignore",
    stderr: "pipe",
  },
});
