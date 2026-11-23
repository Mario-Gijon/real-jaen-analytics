import { afterEach, describe, expect, it, vi } from "vitest";
import { appCookiePath, appPath, routePath } from "@/contract/base-path";

afterEach(() => vi.unstubAllEnvs());

describe("application base path", () => {
  it("keeps development at the site root", () => {
    vi.stubEnv("NEXT_PUBLIC_BASE_PATH", "");
    expect(appPath("/api/v1/session")).toBe("/api/v1/session");
    expect(routePath("/teams")).toBe("/teams");
    expect(appCookiePath()).toBe("/");
  });

  it("prefixes production paths exactly once and scopes session cookies", () => {
    vi.stubEnv("NEXT_PUBLIC_BASE_PATH", "/real-jaen");
    expect(appPath("/api/v1/session")).toBe("/real-jaen/api/v1/session");
    expect(appPath("/real-jaen/api/v1/session")).toBe("/real-jaen/api/v1/session");
    expect(appPath("https://example.test/crest.png")).toBe("https://example.test/crest.png");
    expect(routePath("/real-jaen/teams")).toBe("/teams");
    expect(routePath("/real-jaen")).toBe("/");
    expect(appCookiePath()).toBe("/real-jaen");
  });
});
