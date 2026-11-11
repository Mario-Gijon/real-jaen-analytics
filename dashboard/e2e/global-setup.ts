import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { request, type FullConfig } from "@playwright/test";
import { E2E_PASSWORD, STORAGE_STATE } from "./access";

/** One session for every spec, as an analyst who already entered the shared password (e2e/access.spec.ts: without). */
export default async function globalSetup(config: FullConfig) {
  const baseURL = config.projects[0].use.baseURL!;
  const context = await request.newContext({ baseURL });
  const response = await context.post("/api/v1/session", { data: { password: E2E_PASSWORD }, headers: { Origin: baseURL } });
  if (response.status() !== 204) throw new Error(`Sign-in for the e2e run failed: ${response.status()}`);
  mkdirSync(dirname(STORAGE_STATE), { recursive: true });
  await context.storageState({ path: STORAGE_STATE });
  await context.dispose();
}
