import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, type Page } from '@playwright/test';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export function fixturePath(name: string): string {
  return path.resolve(__dirname, 'fixtures', name);
}

export async function collectPageErrors(page: Page): Promise<string[]> {
  const errors: string[] = [];
  page.on('pageerror', (err) => errors.push(String(err)));
  page.on('console', (msg) => {
    if (msg.type() === 'error') errors.push(msg.text());
  });
  return errors;
}

export async function expectNoConsoleErrors(page: Page, errors: string[]): Promise<void> {
  await page.waitForTimeout(300);
  expect(errors).toEqual([]);
}

/** Loads a model by uploading the analytic JSON fixture through the guide card. */
export async function modelLoaded(page: Page): Promise<void> {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: /mouse_sim/ })).toBeVisible({ timeout: 15_000 });
  await page.getByRole('button', { name: 'Choose geometry file' }).click();
  await page.locator('input[type="file"]').setInputFiles(fixturePath('analytic.json'));
  await expect(page.locator('.model-row')).toHaveCount(1, { timeout: 15_000 });
}
