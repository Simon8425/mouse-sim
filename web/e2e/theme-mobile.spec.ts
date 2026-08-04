import { test, expect, type Page } from '@playwright/test';
import { modelLoaded, collectPageErrors, expectNoConsoleErrors } from './helpers';

test.describe('theme and mobile responsive', () => {
  let page: Page;
  let errors: string[];

  test.beforeEach(async ({ browser }) => {
    page = await browser.newPage();
    errors = await collectPageErrors(page);
  });

  test('controlled console starts in the required dark theme', async () => {
    await page.addInitScript(() => window.localStorage.clear());
    await modelLoaded(page);
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');

    await expectNoConsoleErrors(page, errors);
  });
});

test.describe('mobile viewport', () => {
  test.use({ viewport: { width: 360, height: 800 } });

  test('mobile navigation drawer toggles without horizontal overflow', async ({ page }) => {
    const errors = await collectPageErrors(page);
    await modelLoaded(page);

    const hasOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth > 0,
    );
    expect(hasOverflow).toBe(false);

    const navToggle = page.getByRole('button', { name: 'Toggle model navigator' });
    await navToggle.click();
    await expect(page.locator('.drawer--nav')).toHaveClass(/is-open/);

    await navToggle.click();
    await expect(page.locator('.drawer--nav')).not.toHaveClass(/is-open/);

    await expectNoConsoleErrors(page, errors);
  });
});
