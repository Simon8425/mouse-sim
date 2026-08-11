import { test, expect, type Page } from '@playwright/test';
import { collectPageErrors, expectNoConsoleErrors, fixturePath } from './helpers';

const ASSET_ID = 'f'.repeat(64);

function partsPreview(): Record<string, unknown> {
  return {
    schema_id: 'gms.geometry-preview/1',
    supported: true,
    format: 'step',
    source_units: 'mm',
    geometry: {
      type: 'mesh',
      vertices: [[0, 0, 0], [0.01, 0, 0], [0, 0.01, 0]],
      triangles: [[0, 1, 2]],
      units: 'm',
      transform: {
        rotation: [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        translation: [0, 0, 0],
        units: 'm',
      },
    },
    diagnostics: [],
    source_name: 'parts.step',
    display_asset: {
      asset_id: ASSET_ID,
      url: `/api/geometry/assets/${ASSET_ID}.glb`,
      format: 'glb',
      parts: [
        { id: 'part-0', name: 'Shell Top', color: [0.36, 1, 0.41] },
        { id: 'part-1', name: 'Wheel', color: [1, 0, 0] },
      ],
      parts_url: `/api/geometry/assets/${ASSET_ID}.parts.json`,
    },
  };
}

function partGeometry(id: string, name: string, offset: number): Record<string, unknown> {
  return {
    id,
    name,
    geometry: {
      type: 'mesh',
      vertices: [
        [offset, 0, 0],
        [offset + 0.01, 0, 0],
        [offset, 0.01, 0],
      ],
      triangles: [[0, 1, 2]],
      units: 'm',
      transform: {
        rotation: [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        translation: [0, 0, 0],
        units: 'm',
      },
    },
  };
}

test.describe('kernel STEP part tree', () => {
  let page: Page;
  let errors: string[];

  test.beforeEach(async ({ browser }) => {
    page = await browser.newPage();
    errors = await collectPageErrors(page);
  });

  test('shows per-part rows with eyes and materials', async () => {
    await page.route('**/api/geometry/normalize*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(partsPreview()),
      });
    });
    await page.route(`**/api/geometry/assets/${ASSET_ID}.parts.json`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          parts: [partGeometry('part-0', 'Shell Top', 0), partGeometry('part-1', 'Wheel', 0.05)],
        }),
      });
    });
    await page.route(`**/api/geometry/assets/${ASSET_ID}.glb`, async (route) => {
      await route.fulfill({ status: 404, body: 'not found' });
    });

    await page.goto('/');
    await page.getByRole('button', { name: /Drop geometry file/i }).click();
    await page
      .locator('input[type="file"]')
      .setInputFiles(fixturePath('faceted-cube.step'));

    // The tree is auto-open after upload; parent row + two part rows.
    await page.locator('.model-row--parent').waitFor({ state: 'attached', timeout: 15_000 });
    await page.waitForFunction(() => document.querySelectorAll('.model-row__eye').length >= 3, {
      timeout: 15_000,
    });
    const partRows = page.locator('.model-row:not(.model-row--parent)');
    await expect(partRows).toHaveCount(2);
    await expect(partRows.nth(0)).toContainText('Shell Top');
    await expect(partRows.nth(1)).toContainText('Wheel');

    // Eye toggle hides exactly one part.
    await page.locator('.model-row__eye').nth(1).click();
    await expect(page.locator('.model-row--hidden')).toHaveCount(1);
    await expect(page.locator('.model-row--hidden')).toContainText('Shell Top');
    await page.locator('.model-row__eye').nth(1).click();
    await expect(page.locator('.model-row--hidden')).toHaveCount(0);

    // Selecting a part opens the inspector with its name and surfaces the
    // per-part material selector for that row.
    await partRows.nth(1).click();
    await page.locator('.drawer--inspector.is-open').waitFor({ state: 'attached', timeout: 5000 });
    await expect(page.locator('.inspector-panel__object')).toHaveText('Wheel');
    await expect(page.locator('.model-row__material')).toHaveCount(1);
    await expect(partRows.nth(1).locator('.model-row__material')).toHaveValue('');

    // Per-part material assignment.
    await partRows.nth(1).locator('.model-row__material').selectOption('ABS');
    await expect(partRows.nth(1).locator('.model-row__material')).toHaveValue('ABS');

    await expectNoConsoleErrors(page, errors);
  });
});
