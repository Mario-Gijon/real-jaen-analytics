import { expect, test } from "@playwright/test";
import { expectEntityAwareRowHover } from "./interactive-row";

test("Home changes preparation context by keyboard and opens a recent match", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await expect(page.getByRole("region", { name: "Encuentro de preparación" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 1 })).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Secciones" }).getByRole("link", { name: "Inicio" })).toHaveAttribute("aria-current", "page");
  const trigger = page.getByRole("button", { name: "Cambiar rival" });
  await expect(trigger).toBeEnabled();
  await trigger.focus();
  await page.keyboard.press("Enter");
  const options = page.getByRole("region", { name: "Próximos encuentros del Real Jaén" }).getByRole("button");
  await expect(options).toHaveCount(6);
  const opponent = (await options.nth(1).locator("strong").innerText()).split(" · ")[1];
  await expect(options.first()).toBeFocused();
  await page.keyboard.press("Tab");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\?season=[^&]+&match=/);
  await expect(page.getByRole("region", { name: "Encuentro de preparación" }).getByText(opponent, { exact: true })).toBeVisible();
  await expect(trigger).toBeFocused();
  const url = page.url();
  await page.reload();
  await expect(page.getByRole("region", { name: "Encuentro de preparación" }).getByText(opponent, { exact: true })).toBeVisible();
  await expect(page).toHaveURL(url);
  await expect(page.getByRole("heading", { level: 2, name: `Últimos 5 partidos del ${opponent}` })).toBeVisible();
  await expect(page.getByRole("list", { name: "Últimos partidos del rival" }).getByRole("link")).toHaveCount(15);
  await expect(page.getByText(/Partidos anteriores|Material complementario/)).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.setViewportSize({ width: 390, height: 844 });
  await trigger.click();
  await expect(options.first()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
  // A match opened from Inicio returns to the same season and target.
  await page.getByRole("list", { name: "Últimos partidos del rival" }).getByRole("link", { name: /^Ver partido/ }).first().click();
  await expect(page).toHaveURL(/\/matches\/[^/?]+\?from=home&season=[^&]+&match=[^&]+$/);
  await page.getByRole("link", { name: "Volver a Inicio" }).click();
  await expect(page).toHaveURL(url);
  await expect(page.getByRole("region", { name: "Encuentro de preparación" }).getByText(opponent, { exact: true })).toBeVisible();
});

test("Home gives the target and recent fixtures independent team and match destinations", async ({ page }) => {
  await page.goto("/");
  const target = page.getByRole("region", { name: "Encuentro de preparación" });
  const targetTeams = target.getByRole("link", { name: /^Ver equipo/ });
  await expect(targetTeams).toHaveCount(2);
  for (const index of [0, 1]) {
    const link = targetTeams.nth(index);
    await expect(link.locator("[data-crest]")).toHaveCount(0);
    const href = await link.getAttribute("href");
    const name = (await link.getAttribute("aria-label"))!.replace("Ver equipo ", "");
    await link.click();
    await expect(page).toHaveURL(new RegExp(href!.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "$"));
    await expect(page.getByRole("heading", { level: 2, name })).toBeVisible();
    await page.goBack();
    await expect(target.getByRole("link", { name: /^Ver equipo/ })).toHaveCount(2);
  }

  const first = page.getByRole("list", { name: "Últimos partidos del rival" }).getByRole("listitem").first();
  const teamLinks = first.getByRole("link", { name: /^Ver equipo/ });
  await expect(teamLinks).toHaveCount(2);
  const homeTeamHref = await teamLinks.first().getAttribute("href");
  await teamLinks.first().click();
  await expect(page).toHaveURL(new RegExp(homeTeamHref!.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "$"));
  await page.goBack();
  const awayLink = page.getByRole("list", { name: "Últimos partidos del rival" }).getByRole("listitem").first()
    .getByRole("link", { name: /^Ver equipo/ }).nth(1);
  const awayHref = await awayLink.getAttribute("href");
  await awayLink.click();
  await expect(page).toHaveURL(new RegExp(awayHref!.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "$"));
  await page.goBack();
  const match = page.getByRole("list", { name: "Últimos partidos del rival" }).getByRole("listitem").first()
    .getByRole("link", { name: /^Ver partido/ });
  await match.click();
  await expect(page).toHaveURL(/\/matches\/[^/?]+\?from=home&season=[^&]+&match=[^&]+$/);
});

test("Home recent-match hit areas distinguish team names from the match row", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  const list = page.getByRole("list", { name: "Últimos partidos del rival" });
  const item = list.getByRole("listitem").first();
  const row = item.locator(":scope > div");
  const teamLink = item.getByRole("link", { name: /^Ver equipo/ }).nth(1);
  await expect(teamLink.locator("[data-crest]")).toHaveCount(0);
  await expect(row.locator("a a")).toHaveCount(0);

  const teamHref = new URL((await teamLink.getAttribute("href"))!, page.url()).href;
  let teamBox = await teamLink.boundingBox();
  expect(teamBox).not.toBeNull();
  await page.mouse.click(teamBox!.x + teamBox!.width / 2, teamBox!.y + teamBox!.height / 2);
  await expect(page).toHaveURL(teamHref);
  await page.goBack();
  await expect(list).toBeVisible();

  // The two pixels immediately beside the visible away-team text are still in the row's match action.
  teamBox = await item.getByRole("link", { name: /^Ver equipo/ }).nth(1).boundingBox();
  const rowBox = await row.boundingBox();
  expect(teamBox).not.toBeNull();
  expect(rowBox).not.toBeNull();
  for (const x of [teamBox!.x - 2, teamBox!.x + teamBox!.width + 2]) {
    expect(x).toBeGreaterThan(rowBox!.x);
    expect(x).toBeLessThan(rowBox!.x + rowBox!.width);
    const y = teamBox!.y + teamBox!.height / 2;
    const matchHref = new URL((await item.getByRole("link", { name: /^Ver partido/ }).getAttribute("href"))!, page.url()).href;
    await page.mouse.click(x, y);
    await expect(page).toHaveURL(matchHref);
    await page.goBack();
    await expect(list).toBeVisible();
  }

  const score = item.getByRole("link", { name: /^Ver partido/ });
  const scoreHref = new URL((await score.getAttribute("href"))!, page.url()).href;
  await score.click();
  await expect(page).toHaveURL(scoreHref);
  await page.goBack();
  await expect(list).toBeVisible();

  const crest = row.locator("[data-crest]").first();
  const crestBox = await crest.boundingBox();
  expect(crestBox).not.toBeNull();
  const crestMatchHref = new URL((await item.getByRole("link", { name: /^Ver partido/ }).getAttribute("href"))!, page.url()).href;
  await page.mouse.click(crestBox!.x + crestBox!.width / 2, crestBox!.y + crestBox!.height / 2);
  await expect(page).toHaveURL(crestMatchHref);
});

test("Home recent rows highlight free space but not a hovered team name", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "Hover feedback is checked with a fine pointer.");
  await page.goto("/");
  const row = page.getByRole("list", { name: "Últimos partidos del rival" })
    .getByRole("listitem").first().locator(":scope > div");
  const teamLink = row.getByRole("link", { name: /^Ver equipo/ }).nth(1);
  await expectEntityAwareRowHover(page, row, row.locator(":scope > div").first(), teamLink);
});

test("Home entity links keep keyboard focus and leave platform-modified clicks to the native link", async ({ page }) => {
  await page.goto("/");
  const homeUrl = page.url();
  const teamLink = page.getByRole("region", { name: "Encuentro de preparación" })
    .getByRole("link", { name: /^Ver equipo/ }).first();
  await teamLink.focus();
  await expect(teamLink).toBeFocused();
  expect(await teamLink.evaluate(link => getComputedStyle(link).outlineStyle)).not.toBe("none");
  await teamLink.evaluate(link => {
    link.addEventListener("click", nativeEvent => {
      const event = nativeEvent as MouseEvent;
      const anchor = event.currentTarget as HTMLAnchorElement;
      setTimeout(() => {
        anchor.dataset.modifiedClick = JSON.stringify({
          ctrlKey: event.ctrlKey, metaKey: event.metaKey, defaultPrevented: event.defaultPrevented,
        });
      }, 0);
    }, { capture: true, once: true });
  });
  await teamLink.click({ modifiers: ["Control"] });
  await expect(page).toHaveURL(homeUrl);
  await expect(teamLink).toHaveAttribute("data-modified-click", JSON.stringify({
    ctrlKey: true, metaKey: false, defaultPrevented: false,
  }));
});

test("Ver todos preserves season and opens all rounds for the selected rival", async ({ page }) => {
  await page.goto("/");
  const link = page.getByRole("region", { name: /^Últimos 5 partidos del / }).getByRole("link", { name: "Ver todos" });
  await expect(link).toBeVisible();
  const context = new URL((await link.getAttribute("href"))!, page.url());
  await link.click();
  await expect(page.getByRole("combobox", { name: "Temporada", exact: true })).toHaveValue(context.searchParams.get("season")!);
  await expect(page.getByRole("combobox", { name: "Equipo", exact: true })).toHaveValue(context.searchParams.get("team")!);
  await expect(page.getByRole("combobox", { name: "Jornada", exact: true })).toHaveValue("all");
});

test("Home summary centers three equal columns on desktop and tablet, then stacks them on phones", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await page.getByRole("button", { name: "Cambiar rival" }).click();
  await page.getByRole("region", { name: "Próximos encuentros del Real Jaén" }).getByRole("button").nth(1).click();
  const summary = page.locator('dl[aria-label="Síntesis de partidos recientes"]');
  await expect(summary).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("summary-desktop.png"), fullPage: true });

  const inspect = () => summary.evaluate(element => {
    const container = element.getBoundingClientRect();
    const groups = Array.from(element.children, group => {
      const rect = group.getBoundingClientRect();
      const title = group.querySelector("dt")!.getBoundingClientRect();
      const values = group.querySelector("dd")!.getBoundingClientRect();
      return { width: rect.width, center: rect.left + rect.width / 2, titleTop: title.top,
        titleCenter: title.left + title.width / 2, valuesCenter: values.left + values.width / 2 };
    });
    return { width: container.width, columns: getComputedStyle(element).gridTemplateColumns.split(" ").length, groups };
  });
  const expectCentered = (groups: Awaited<ReturnType<typeof inspect>>["groups"]) => {
    for (const group of groups) {
      expect(Math.abs(group.titleCenter - group.center)).toBeLessThanOrEqual(1);
      expect(Math.abs(group.valuesCenter - group.center)).toBeLessThanOrEqual(1);
    }
  };

  const desktop = await inspect();
  expect(desktop.columns).toBe(3);
  expect(Math.max(...desktop.groups.map(group => group.width)) - Math.min(...desktop.groups.map(group => group.width))).toBeLessThanOrEqual(1);
  expectCentered(desktop.groups);
  expect(Math.max(...desktop.groups.map(group => group.titleTop)) - Math.min(...desktop.groups.map(group => group.titleTop))).toBeLessThanOrEqual(1);

  await page.setViewportSize({ width: 768, height: 900 });
  const tablet = await inspect();
  expect(tablet.columns).toBe(3);
  expect(Math.max(...tablet.groups.map(group => group.width)) - Math.min(...tablet.groups.map(group => group.width))).toBeLessThanOrEqual(1);
  expectCentered(tablet.groups);
  expect(Math.max(...tablet.groups.map(group => group.titleTop)) - Math.min(...tablet.groups.map(group => group.titleTop))).toBeLessThanOrEqual(1);

  await page.setViewportSize({ width: 390, height: 844 });
  const phone = await inspect();
  expect(phone.columns).toBe(1);
  expectCentered(phone.groups);
  const phoneCenters = phone.groups.map(group => group.center);
  expect(Math.max(...phoneCenters) - Math.min(...phoneCenters)).toBeLessThanOrEqual(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({ path: testInfo.outputPath("summary-phone.png"), fullPage: true });
});
