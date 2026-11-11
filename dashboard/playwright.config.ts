import { join } from "node:path";
import { defineConfig, devices } from "@playwright/test";
import { ACCESS_FIXTURES, STORAGE_STATE } from "./e2e/access";

/** Desktop (1440 px) and phone (390 px) checks against the production build. */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  reporter: "list",
  // The whole application asks for the shared password: one sign-in, reused by every spec (e2e/global-setup.ts).
  globalSetup: "./e2e/global-setup.ts",
  use: { baseURL: "http://127.0.0.1:3100", storageState: STORAGE_STATE },
  projects: [
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 1000 } },
    },
    {
      name: "mobile",
      use: { ...devices["Desktop Chrome"], viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true },
    },
  ],
  webServer: {
    command: "npm run build && npx next start -p 3100",
    // Always the demonstration, whatever a local .env.local selects; the real-data specs simulate the API themselves.
    // Access configured with test-only files, so the gate is exercised as in a real installation.
    env: { DATA_MODE: "demo", RJ_ACCESS_PASSWORD_FILE: join(ACCESS_FIXTURES, "access-password"),
      RJ_SESSION_SECRET_FILE: join(ACCESS_FIXTURES, "session-secret") },
    url: "http://127.0.0.1:3100/api/v1/session",
    reuseExistingServer: !process.env.CI,
    timeout: 240_000,
  },
});
