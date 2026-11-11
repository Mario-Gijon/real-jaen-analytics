import { expect, test } from "@playwright/test";
import { E2E_PASSWORD } from "./access";

// Without the shared session of the other specs: as a browser that never entered the password.
test.use({ storageState: { cookies: [], origins: [] } });

test("Acceso: every page and data route asks for the password; the session returns to the page and can be closed", async ({ page, request }) => {
  expect((await request.get("/api/v1/source")).status()).toBe(401);
  await page.goto("/teams?view=observed");
  await expect(page).toHaveURL(/\/acceso\?next=%2Fteams%3Fview%3Dobserved$/);
  await expect(page.getByRole("heading", { name: "Bienvenido" })).toBeVisible();
  // Nothing readable before signing in: no navigation, data notice or controls in the top bar.
  await expect(page.getByRole("navigation")).toHaveCount(0);
  await page.getByLabel("Contraseña").fill("no-es-la-contrasena");
  await page.getByRole("button", { name: "Entrar" }).click();
  await expect(page.getByRole("main").getByRole("alert")).toHaveText("Contraseña incorrecta.");
  await page.getByLabel("Contraseña").fill(E2E_PASSWORD);
  await page.getByRole("button", { name: "Entrar" }).click();
  await expect(page).toHaveURL(/\/teams\?view=observed$/);
  const [cookie] = (await page.context().cookies()).filter((c) => c.name === "rj_session");
  expect(cookie).toMatchObject({ httpOnly: true, sameSite: "Strict", path: "/" });
  expect(cookie.expires - Date.now() / 1000).toBeGreaterThan(89 * 24 * 3600);
  expect(JSON.stringify(await page.evaluate(() => ({ ...localStorage, ...sessionStorage })))).not.toContain(E2E_PASSWORD);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(overflow).toBe(false);
  await page.getByRole("banner").getByRole("button", { name: "Cerrar sesión" }).click();
  // Straight to the access screen, or with the way back if a request of the page noticed the closed session first.
  await expect(page).toHaveURL(/\/acceso(\?next=%2Fteams.*)?$/);
  await expect(page.getByLabel("Contraseña")).toBeVisible();
  await page.goto("/matches");
  await expect(page).toHaveURL(/\/acceso\?next=%2Fmatches$/);
});
