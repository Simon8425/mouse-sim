import { test, expect, type Page } from '@playwright/test';
import { modelLoaded, collectPageErrors, expectNoConsoleErrors } from './helpers';

test.describe('model workspace', () => {
  let page: Page;
  let errors: string[];

  test.beforeEach(async ({ browser }) => {
    page = await browser.newPage();
    errors = await collectPageErrors(page);
  });

  test('geometry upload loads model and canvas', async () => {
    await modelLoaded(page);
    await expect(page.locator('.scene-viewport canvas')).toBeVisible();
    await expect(page.locator('.status-live')).toHaveText('Complete', { timeout: 15_000 });
    await expectNoConsoleErrors(page, errors);
  });

  test('selection synchronizes tree and inspector', async () => {
    await modelLoaded(page);
    await page.getByRole('button', { name: 'Toggle model navigator' }).click();
    await page.locator('.model-row__name', { hasText: 'analytic' }).click();
    await expect(page.locator('.model-row[aria-selected="true"]')).toContainText('analytic');
    await page.getByRole('button', { name: 'Toggle inspector' }).click();
    await expect(page.locator('.inspector-panel')).toContainText('analytic');
  });

  test('fit and exploded actions work', async () => {
    await modelLoaded(page);
    const exploded = page.getByRole('button', { name: 'Exploded' });
    await expect(exploded).toHaveAttribute('aria-pressed', 'false');
    await expect(page.locator('.display-only-label')).toBeVisible();
    await exploded.click();
    await expect(exploded).toHaveAttribute('aria-pressed', 'true');
    await exploded.click();
    await expect(exploded).toHaveAttribute('aria-pressed', 'false');
    await page.locator('.viewport-toolbar').getByRole('button', { name: 'Fit view' }).click();
  });

  test('result rail renders tabs and qualification disposition', async () => {
    await modelLoaded(page);
    await expect(page.locator('.status-live')).toHaveText('Complete', { timeout: 15_000 });
    await page.getByRole('tab', { name: 'qualification' }).click();
    await expect(page.getByText('Exploration only')).toBeVisible();
    await page.getByRole('tab', { name: 'structural' }).click();
    await expect(page.getByText('No structural analysis was requested.')).toBeVisible();
    await page.getByRole('tab', { name: 'issues' }).click();
    await expect(page.getByText('No issues or validation findings reported.')).toBeVisible();
  });

  test('mode switch triggers rerun and qualification gate view', async () => {
    await modelLoaded(page);
    await expect(page.locator('.status-live')).toHaveText('Complete', { timeout: 15_000 });
    await page.getByRole('button', { name: 'Qualification' }).click();
    await expect(page.locator('.status-live')).toHaveText('Complete', { timeout: 15_000 });
    await expect(page.locator('.results-rail')).toContainText(/blocked|pending review|exploration only/i);
    await expectNoConsoleErrors(page, errors);
  });
});
