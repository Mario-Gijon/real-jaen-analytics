import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { randomBytes } from "node:crypto";
import { join } from "node:path";

/** Test-only access files of the e2e server (never a real installation's secrets). */
export const ACCESS_FIXTURES = join(tmpdir(), "rj-analyst-e2e", "access");
mkdirSync(ACCESS_FIXTURES, { recursive: true, mode: 0o700 });
const passwordFile = join(ACCESS_FIXTURES, "access-password");
const sessionSecretFile = join(ACCESS_FIXTURES, "session-secret");
for (const [file, value] of [
  [passwordFile, randomBytes(32).toString("base64url")],
  [sessionSecretFile, randomBytes(32).toString("hex")],
] as const) {
  if (!existsSync(file)) writeFileSync(file, value, { flag: "wx", mode: 0o600 });
}
export const E2E_PASSWORD = readFileSync(join(ACCESS_FIXTURES, "access-password"), "utf8").trim();
/** The signed-in browser state shared by the specs, outside the repository. */
export const STORAGE_STATE = join(tmpdir(), "rj-analyst-e2e", "session.json");
