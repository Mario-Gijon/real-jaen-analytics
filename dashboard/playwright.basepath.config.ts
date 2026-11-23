import { defineConfig, devices } from "@playwright/test";
import { ACCESS_FIXTURES } from "./e2e/access";

const basePath = "/real-jaen";
const origin = "http://127.0.0.1:3101";

/** Production-prefix smoke checks against a real Next.js build, without Docker or production services. */
export default defineConfig({
  testDir: "./e2e-basepath",
  fullyParallel: false,
  reporter: "list",
  use: { ...devices["Desktop Chrome"], baseURL: origin },
  webServer: {
    command: "npm run build && npx next start -H 127.0.0.1 -p 3101",
    env: {
      NEXT_PUBLIC_BASE_PATH: basePath,
      DATA_MODE: "demo",
      RJ_ACCESS_PASSWORD_FILE: `${ACCESS_FIXTURES}/access-password`,
      RJ_UPDATE_PASSWORD_FILE: `${ACCESS_FIXTURES}/update-password`,
      RJ_SESSION_SECRET_FILE: `${ACCESS_FIXTURES}/session-secret`,
    },
    url: `${origin}${basePath}/api/v1/session`,
    reuseExistingServer: !process.env.CI,
    timeout: 240_000,
  },
});
