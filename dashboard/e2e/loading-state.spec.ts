import { expect, test } from "@playwright/test";

test("page loading is centered, responsive and respects reduced motion", async ({ page }, testInfo) => {
  let release!: () => void;
  let markStarted!: () => void;
  const responseGate = new Promise<void>((resolve) => { release = resolve; });
  const requestStarted = new Promise<void>((resolve) => { markStarted = resolve; });
  await page.route("**/api/v1/source", async (route) => {
    markStarted();
    await responseGate;
    await route.fallback();
  });

  await page.goto("/", { waitUntil: "domcontentloaded" });
  await requestStarted;
  const status = page.getByRole("status").filter({ hasText: "Cargando preparación…" });
  await expect(status).toBeVisible();
  await expect(status).toHaveCSS("opacity", "1");
  await expect(status.locator("[aria-hidden='true']")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("loading-page-" + testInfo.project.name + ".png"), fullPage: true });

  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(status.locator("[aria-hidden='true']")).toHaveCSS("animation-name", "none");
  await page.screenshot({ path: testInfo.outputPath("loading-page-reduced-motion-" + testInfo.project.name + ".png"), fullPage: true });

  release();
  await expect(page.getByRole("navigation", { name: "Secciones" })).toBeVisible();
  await expect(status).toHaveCount(0);
});

test("section loading stays contextual through a slow response, error and retry", async ({ page }) => {
  let release!: () => void;
  let markStarted!: () => void;
  const responseGate = new Promise<void>((resolve) => { release = resolve; });
  const requestStarted = new Promise<void>((resolve) => { markStarted = resolve; });
  let attempts = 0;
  await page.route("**/api/v1/competitions", async (route) => {
    attempts += 1;
    if (attempts === 1) {
      markStarted();
      await responseGate;
      return route.fulfill({ status: 503, json: { error: { code: "UPSTREAM_UNAVAILABLE", message: "Temporalmente no disponible." } } });
    }
    return route.fallback();
  });

  await page.goto("/matches", { waitUntil: "domcontentloaded" });
  await requestStarted;
  await expect(page.getByRole("navigation", { name: "Secciones" }).getByRole("link", { name: "Partidos" }))
    .toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("combobox", { name: "Competición" })).toBeVisible();
  const region = page.getByRole("region", { name: "Competiciones" });
  const status = region.getByRole("status");
  await expect(region).toHaveAttribute("aria-busy", "true");
  await expect(status).toHaveText("Cargando competiciones…");
  await expect(region.getByRole("status")).toHaveCount(1);

  release();
  await expect(page.getByText("No se pudieron cargar las competiciones.")).toBeVisible();
  await expect(region).toHaveCount(0);
  await page.getByRole("button", { name: "Reintentar" }).click();
  await expect(page.getByRole("combobox", { name: "Jornada" })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "Cargando competiciones…" })).toHaveCount(0);
  expect(attempts).toBe(2);
});

test("a quick response leaves no loading indicator behind", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "Secciones" })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "Cargando preparación…" })).toHaveCount(0);
});
