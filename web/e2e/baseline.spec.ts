import { test, expect, type Page } from '@playwright/test';
import {
  modelLoaded,
  collectPageErrors,
  expectNoConsoleErrors,
  expectRunComplete,
  runStandardTest,
  openResultsRail,
} from './helpers';

test.describe('model workspace', () => {
  let page: Page;
  let errors: string[];

  test.beforeEach(async ({ browser }) => {
    page = await browser.newPage();
    errors = await collectPageErrors(page);
  });

  test('starts with an empty scene and no demo objects', async () => {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: /mouse\s*sim/i })).toHaveCount(1, { timeout: 15_000 });
    await expect(page.locator('.model-row')).toHaveCount(0, { timeout: 15_000 });
    // The empty-state message lives inside the navigator drawer, which starts
    // closed; open it to verify the empty tree state.
    await page.getByRole('button', { name: 'Toggle model navigator' }).click();
    await expect(page.getByText('No geometry loaded')).toBeVisible();
    await expectNoConsoleErrors(page, errors);
  });

  test('geometry upload loads model and canvas', async () => {
    await modelLoaded(page);
    // Under parallel-suite CPU load the scene mount can be slow; 15 s mirrors
    // the other mount assertions instead of the default 5 s.
    await expect(page.locator('.scene-viewport canvas')).toBeVisible({ timeout: 15_000 });
    await runStandardTest(page);
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

  test('result rail shows the verdict, key metrics, and issues after a run', async () => {
    await modelLoaded(page);
    await runStandardTest(page);
    await expectRunComplete(page);
    // The navigator auto-opens after upload; on narrow viewports the open
    // drawer overlays the rail, so close it before interacting.
    if (await page.locator('.drawer--nav.is-open').count()) {
      await page.getByRole('button', { name: 'Toggle model navigator' }).click();
    }
    await openResultsRail(page);
    await expect(page.locator('.results-rail')).toContainText(/PASS|WARN|FAIL/);
    await expect(page.locator('.results-rail')).toContainText('Material:');
    await expect(page.locator('.results-rail')).toContainText('Safety factor');
    await expect(page.locator('.results-rail')).toContainText('Impact force');
    // The analytic fixture parts carry no explicit materials, so the default
    // material fallback surfaces as an actionable warning.
    await expect(page.locator('.results-rail')).toContainText(/Default material/i);
  });

  test('canvas picking works with the results rail collapsed', async () => {
    await modelLoaded(page);
    // The navigator auto-opens after upload; on narrow viewports the open
    // drawer overlays the viewport, so close it before interacting with
    // viewport actions.
    if (await page.locator('.drawer--nav.is-open').count()) {
      await page.getByRole('button', { name: 'Toggle model navigator' }).click();
    }
    const viewport = page.locator('.scene-viewport');
    // Slow scene mount under parallel-suite CPU load; 15 s mirrors the other
    // mount assertions instead of the default 5 s.
    await expect(viewport).toBeVisible({ timeout: 15_000 });
    // The rail must sit as a slim right-edge strip, never over the canvas.
    await expect(page.locator('.results-rail--collapsed')).toBeVisible({ timeout: 15_000 });

    const box = await viewport.boundingBox();
    expect(box).not.toBeNull();
    if (!box) return;

    // Click the canvas center; the picker must hit the framed object.
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    await expect(page.locator('.model-row[aria-selected="true"]')).toHaveCount(1, { timeout: 5000 });

    // A second pick on the canvas must also land (the collapsed rail strip
    // must never intercept pointer events destined for the viewport). The
    // inspector drawer opens after the first pick and can resize the
    // viewport, so re-measure the canvas before the second click.
    const boxAfter = await viewport.boundingBox();
    if (boxAfter) {
      await page.mouse.click(boxAfter.x + boxAfter.width / 2, boxAfter.y + boxAfter.height / 2);
    }
    await expect(page.locator('.model-row[aria-selected="true"]')).toHaveCount(1, { timeout: 5000 });
    await expectNoConsoleErrors(page, errors);
  });
});
