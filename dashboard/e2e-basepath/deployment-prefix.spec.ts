import { expect, test } from "@playwright/test";
import { E2E_PASSWORD } from "../e2e/access";

test("production basePath covers authentication, cookies, API, navigation and assets", async ({ page }) => {
  const apiPaths: string[] = [];
  page.on("request", request => {
    const url = new URL(request.url());
    if (url.pathname.includes("/api/v1/")) apiPaths.push(url.pathname);
  });

  await page.goto("/real-jaen/teams?tab=players");
  await expect(page).toHaveURL(/\/real-jaen\/acceso\?next=%2Fteams%3Ftab%3Dplayers$/);
  await page.getByLabel("Contraseña").fill(E2E_PASSWORD);
  await page.getByRole("button", { name: "Entrar" }).click();
  await expect(page).toHaveURL(/\/real-jaen\/teams\?tab=players$/);

  const session = await page.request.get("/real-jaen/api/v1/session");
  expect(session.ok()).toBeTruthy();
  expect(await session.json()).toMatchObject({ required: true, signedIn: true });
  expect((await page.context().cookies()).find(cookie => cookie.name === "rj_session")?.path).toBe("/real-jaen");

  const brandImage = page.locator("header img").first();
  await expect(brandImage).toHaveAttribute("src", /\/real-jaen\/_next\/image\?url=%2Freal-jaen%2Fcrests%2F/);
  await expect.poll(async () => brandImage.evaluate(element => (element as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
  await page.getByRole("link", { name: "Partidos", exact: true }).click();
  await expect(page).toHaveURL(/\/real-jaen\/matches$/);
  expect(apiPaths.length).toBeGreaterThan(0);
  expect(apiPaths.every(path => path.startsWith("/real-jaen/api/v1/"))).toBeTruthy();
  expect((await page.request.get("/real-jaen/api/v1/updates/current")).status()).toBe(403);

  await page.goto("/real-jaen/operador");
  await expect(page).toHaveURL(/\/real-jaen\/?$/);
});
