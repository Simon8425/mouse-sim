import { test, expect, type Page } from '@playwright/test';
import { modelLoaded, collectPageErrors, expectNoConsoleErrors, expectRunComplete } from './helpers';

test.describe('model workspace', () => {
  let page: Page;
  let errors: string[];

  test.beforeEach(async ({ browser }) => {
    page = await browser.newPage();
    errors = await collectPageErrors(page);
  });

  test('starts with an empty scene and no demo objects', async () => {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: /mouse_sim/ })).toHaveCount(1, { timeout: 15_000 });
    await expect(page.locator('.model-row')).toHaveCount(0, { timeout: 15_000 });
    await expect(page.getByText('No geometry loaded')).toBeVisible();
    await expectNoConsoleErrors(page, errors);
  });

  test('geometry upload loads model and canvas', async () => {
    await modelLoaded(page);
    await expect(page.locator('.scene-viewport canvas')).toBeVisible();
    await expectRunComplete(page);
    await expectNoConsoleErrors(page, errors);
  });

  test('selection synchronizes tree and inspector', async () => {
    await modelLoaded(page);
    await page.locator('.model-row__name', { hasText: 'analytic' }).click();
    await expect(page.locator('.model-row[aria-selected="true"]')).toContainText('analytic');
    await page.getByRole('button', { name: 'Toggle inspector' }).click();
    await expect(page.locator('.inspector-panel')).toContainText('analytic');
  });

  test('fit and exploded actions work', async ({ viewport }) => {
    await modelLoaded(page);
    // The navigator auto-opens after upload; on narrow viewports the open
    // drawer overlays the viewport toolbar, so close it before interacting
    // with viewport actions.
    if (await page.locator('.drawer--nav.is-open').count()) {
      await page.getByRole('button', { name: 'Toggle model navigator' }).click();
    }
    // The exploded toggle group is hidden below 760px (styles.css responsive
    // rules), so its behavior is only asserted on wider viewports.
    if ((viewport?.width ?? 1440) >= 760) {
      const exploded = page.getByRole('button', { name: 'Exploded' });
      await expect(exploded).toHaveAttribute('aria-pressed', 'false');
      await expect(page.locator('.display-only-label')).toBeVisible();
      await exploded.click();
      await expect(exploded).toHaveAttribute('aria-pressed', 'true');
      await exploded.click();
      await expect(exploded).toHaveAttribute('aria-pressed', 'false');
    }
    await page.locator('.viewport-toolbar').getByRole('button', { name: 'Fit view' }).click();
  });

  test('result rail renders tabs and qualification disposition', async () => {
    await modelLoaded(page);
    await expectRunComplete(page);
    await page.getByRole('tab', { name: 'qualification' }).click();
    await expect(
      page.getByRole('row', { name: 'Evidence disposition' }).getByRole('status'),
    ).toHaveText('Exploration only');
    await page.getByRole('tab', { name: 'structural' }).click();
    await expect(page.getByText(/No structural (evaluation|response)/i)).toBeVisible();
    await page.getByRole('tab', { name: 'issues' }).click();
    await expect(page.getByText('No issues or validation findings reported.')).toBeVisible();
  });

  test('mode switch triggers rerun and qualification gate view', async () => {
    await modelLoaded(page);
    await expectRunComplete(page);
    await page.getByRole('button', { name: 'RUN QUALIFICATION' }).click();
    await expectRunComplete(page);
    await expect(page.locator('.results-rail')).toContainText(/blocked|pending review|exploration only/i);
    await expectNoConsoleErrors(page, errors);
  });
});
