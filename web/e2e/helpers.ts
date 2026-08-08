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
    if (msg.type() !== 'error') return;
    // Chromium logs every non-2xx response as "Failed to load resource"; the
    // app deliberately accepts 422 geometry-preview envelopes, so this
    // automatic network noise is not a real console error.
    if (msg.text().startsWith('Failed to load resource')) return;
    errors.push(msg.text());
  });
  return errors;
}

export async function expectNoConsoleErrors(page: Page, errors: string[]): Promise<void> {
  await page.waitForTimeout(300);
  expect(errors).toEqual([]);
}

/** Verifies the clean start screen, then loads the analytic fixture for a test. */
export async function modelLoaded(page: Page): Promise<void> {
  await page.goto('/');
  // The project heading is intentionally hidden by the compact mobile layout;
  // presence confirms the app booted without requiring a desktop-only element.
  await expect(page.getByRole('heading', { name: /mouse_sim/ })).toHaveCount(1, { timeout: 15_000 });
  await expect(page.locator('.model-row')).toHaveCount(0, { timeout: 15_000 });
  await page.getByRole('button', { name: 'Choose geometry file' }).click();
  await page.locator('input[type="file"]').setInputFiles(fixturePath('analytic.json'));
  await expect(page.locator('.model-row')).toHaveCount(1, { timeout: 15_000 });
}

/** Waits until the analysis run reports 'Complete' in the Settings panel. */
export async function expectRunComplete(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Control panel' }).click();
  await expect(page.locator('.mission-control')).toContainText('Complete', { timeout: 15_000 });
  await page.getByRole('button', { name: 'Close settings panel' }).click();
}
