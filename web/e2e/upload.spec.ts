import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test, expect, type Page } from '@playwright/test';
import { modelLoaded, fixturePath, collectPageErrors, expectNoConsoleErrors, expectRunComplete } from './helpers';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

test.describe('upload flow', () => {
  let page: Page;
  let errors: string[];

  test.beforeEach(async ({ browser }) => {
    page = await browser.newPage();
    errors = await collectPageErrors(page);
  });

  test('OBJ upload normalizes and updates model tree', async () => {
    await modelLoaded(page);
    await page.getByRole('button', { name: 'Upload geometry' }).click();
    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles(path.resolve(__dirname, 'fixtures', 'cover.obj'));
    await page.locator('.dropzone-unit-selector select').selectOption('mm');
    await page.getByRole('button', { name: 'Import Geometry' }).click();

    await expect(page.locator('.model-row')).toHaveCount(1, { timeout: 15_000 });
    await expect(page.locator('.model-row')).toContainText('cover.obj');
    await expectRunComplete(page);
    await expectNoConsoleErrors(page, errors);
  });

  test('STEP upload normalizes and updates model tree', async () => {
    await modelLoaded(page);
    await page.getByRole('button', { name: 'Upload geometry' }).click();
    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles(fixturePath('faceted-cube.step'));

    await expect(page.locator('.model-row')).toHaveCount(1, { timeout: 15_000 });
    await expect(page.locator('.model-row')).toContainText('faceted-cube.step');
    await expectRunComplete(page);
    await expectNoConsoleErrors(page, errors);
  });

  test('Advanced STEP upload displays structured diagnostic', async () => {
    await modelLoaded(page);
    await page.getByRole('button', { name: 'Upload geometry' }).click();
    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles(fixturePath('advanced-brep.step'));
    await expect(page.locator('.dropzone-error')).toContainText(/unsupported|STEP|B-rep/i, { timeout: 10_000 });
    await expectNoConsoleErrors(page, errors);
  });

  test('Malformed STEP upload displays parse diagnostic', async () => {
    await modelLoaded(page);
    await page.getByRole('button', { name: 'Upload geometry' }).click();
    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles(path.resolve(__dirname, 'fixtures', 'unsupported.step'));
    await expect(page.locator('.dropzone-error')).toContainText(/parse|STEP/i, { timeout: 10_000 });
    await expectNoConsoleErrors(page, errors);
  });

  test('analytic JSON upload normalizes immediately', async () => {
    await modelLoaded(page);
    await page.getByRole('button', { name: 'Upload geometry' }).click();
    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles(fixturePath('analytic.json'));

    await expect(page.locator('.model-row')).toHaveCount(1, { timeout: 15_000 });
    await expect(page.locator('.model-row')).toContainText('analytic');
    await expectRunComplete(page);
    await expectNoConsoleErrors(page, errors);
  });
});
