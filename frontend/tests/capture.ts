import { type Page } from "@playwright/test";

export async function capture(page: Page, filename: string, fullPage = true) {
  await page.evaluate(() => document.fonts.ready.then(() => undefined));
  await page.screenshot({
    path: `../docs/frontend/screenshots/${filename}.png`,
    fullPage,
    animations: "disabled",
  });
}
