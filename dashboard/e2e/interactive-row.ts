import { expect, type Locator, type Page } from "@playwright/test";

/** Verifies free-space row feedback, then the link's independent hover state. */
export async function expectEntityAwareRowHover(
  page: Page,
  row: Locator,
  freeZone: Locator,
  entityLink: Locator,
) {
  await row.scrollIntoViewIfNeeded();
  const rowBox = await row.boundingBox();
  const freeBox = await freeZone.boundingBox();
  const linkBox = await entityLink.boundingBox();
  expect(rowBox).not.toBeNull();
  expect(freeBox).not.toBeNull();
  expect(linkBox).not.toBeNull();
  const stableBox = rowBox!;
  const expectStableGeometry = async () => {
    expect(await row.boundingBox()).toEqual(stableBox);
  };

  const freePoint = { x: freeBox!.x + freeBox!.width / 2, y: freeBox!.y + freeBox!.height / 2 };
  const linkPoint = { x: linkBox!.x + linkBox!.width / 2, y: linkBox!.y + linkBox!.height / 2 };
  expect(freePoint.x < linkBox!.x || freePoint.x > linkBox!.x + linkBox!.width
    || freePoint.y < linkBox!.y || freePoint.y > linkBox!.y + linkBox!.height).toBe(true);

  const hoverColor = await page.evaluate(() => {
    const probe = document.createElement("span");
    probe.style.cssText = "position:fixed;visibility:hidden;background:var(--rj-bg-hover)";
    document.body.append(probe);
    const color = getComputedStyle(probe).backgroundColor;
    probe.remove();
    return color;
  });
  const baseBackground = await row.evaluate((element) => getComputedStyle(element).backgroundColor);
  const baseTextColor = await entityLink.evaluate((element) => {
    const leaves = [...element.querySelectorAll("*")].filter((child) => child.children.length === 0 && child.textContent?.trim());
    return getComputedStyle(leaves[0] ?? element).color;
  });

  await page.mouse.move(freePoint.x, freePoint.y);
  await expect.poll(() => row.evaluate((element) => getComputedStyle(element).backgroundColor)).toBe(hoverColor);
  await expectStableGeometry();
  expect(await page.evaluate(({ x, y }) => {
    const target = document.elementFromPoint(x, y);
    return target ? getComputedStyle(target).cursor : "";
  }, freePoint)).toBe("pointer");

  await page.mouse.move(linkPoint.x, linkPoint.y);
  await expect.poll(() => row.evaluate((element) => getComputedStyle(element).backgroundColor)).toBe(baseBackground);
  await expectStableGeometry();
  const linkStyle = await entityLink.evaluate((element) => {
    const leaves = [...element.querySelectorAll("*")].filter((child) => child.children.length === 0 && child.textContent?.trim());
    const target = leaves[0] ?? element;
    return { cursor: getComputedStyle(element).cursor, color: getComputedStyle(target).color };
  });
  expect(linkStyle.cursor).toBe("pointer");
  expect(linkStyle.color).not.toBe(baseTextColor);

  await page.mouse.move(freePoint.x, freePoint.y);
  await expect.poll(() => row.evaluate((element) => getComputedStyle(element).backgroundColor)).toBe(hoverColor);
  await expectStableGeometry();
}

/** A row with no delegated action leaves feedback and pointer behavior on the name link alone. */
export async function expectInlineEntityHover(
  page: Page,
  row: Locator,
  freeZone: Locator,
  entityLink: Locator,
) {
  await row.scrollIntoViewIfNeeded();
  const rowBox = await row.boundingBox();
  const freeBox = await freeZone.boundingBox();
  const linkBox = await entityLink.boundingBox();
  expect(rowBox).not.toBeNull();
  expect(freeBox).not.toBeNull();
  expect(linkBox).not.toBeNull();

  const freePoint = { x: freeBox!.x + freeBox!.width / 2, y: freeBox!.y + freeBox!.height / 2 };
  const linkPoint = { x: linkBox!.x + linkBox!.width / 2, y: linkBox!.y + linkBox!.height / 2 };
  const baseBackground = await row.evaluate((element) => getComputedStyle(element).backgroundColor);
  const baseTextColor = await entityLink.evaluate((element) => {
    const leaves = [...element.querySelectorAll("*")].filter((child) => child.children.length === 0 && child.textContent?.trim());
    return getComputedStyle(leaves[0] ?? element).color;
  });

  await page.mouse.move(freePoint.x, freePoint.y);
  await expect.poll(() => row.evaluate((element) => getComputedStyle(element).backgroundColor)).toBe(baseBackground);
  expect(await page.evaluate(({ x, y }) => {
    const target = document.elementFromPoint(x, y);
    return target ? getComputedStyle(target).cursor : "";
  }, freePoint)).not.toBe("pointer");

  await page.mouse.move(linkPoint.x, linkPoint.y);
  await expect.poll(() => row.evaluate((element) => getComputedStyle(element).backgroundColor)).toBe(baseBackground);
  const linkStyle = await entityLink.evaluate((element) => {
    const leaves = [...element.querySelectorAll("*")].filter((child) => child.children.length === 0 && child.textContent?.trim());
    const target = leaves[0] ?? element;
    return { cursor: getComputedStyle(element).cursor, color: getComputedStyle(target).color };
  });
  expect(linkStyle.cursor).toBe("pointer");
  expect(linkStyle.color).not.toBe(baseTextColor);
}

export async function expectKeyboardFocus(page: Page, control: Locator) {
  await control.focus();
  await page.keyboard.press("Shift+Tab");
  await page.keyboard.press("Tab");
  await expect(control).toBeFocused();
  expect(await control.evaluate((element) => getComputedStyle(element).outlineStyle)).not.toBe("none");
}
