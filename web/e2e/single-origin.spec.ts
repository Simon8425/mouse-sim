import { test, expect } from '@playwright/test';
import { collectPageErrors, expectNoConsoleErrors, fixturePath } from './helpers';

test.describe('single-origin production server', () => {
  test('serves built SPA and API on single port', async ({ page, request }) => {
    const errors = await collectPageErrors(page);
    await page.goto('http://127.0.0.1:8898/');

    await expect(page.getByRole('heading', { name: /mouse_sim/ })).toHaveCount(1);
    await page.getByRole('button', { name: 'Upload geometry' }).click();
    await page.locator('input[type="file"]').setInputFiles(fixturePath('analytic.json'));
    await expect(page.locator('.model-row')).toHaveCount(1, { timeout: 15_000 });
    await expect(page.locator('.run-status__value')).toHaveText('Complete', { timeout: 15_000 });

    const apiRes = await request.get('http://127.0.0.1:8898/api/health');
    expect(apiRes.ok()).toBe(true);

    await expectNoConsoleErrors(page, errors);
  });
});
