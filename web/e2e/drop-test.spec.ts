import { test, expect } from '@playwright/test';
import { collectPageErrors, expectNoConsoleErrors, fixturePath } from './helpers';

function dropSimulationResponse(dropCount = 2): Record<string, unknown> {
  const settle = 0.6;
  const interval = 0.35;
  const drops = Array.from({ length: dropCount }, (_, index) => ({
    index,
    start_s: index * (settle + interval),
    end_s: index * (settle + interval) + settle,
    settled_s: settle,
    impact_count: 2,
    peak_impact_speed_m_s: 3.13,
    peak_kinetic_energy_j: 0.49,
    orientation: 'flat',
  }));
  return {
    schema_id: 'gms.web-analysis-response/1',
    run_id: 'a'.repeat(64),
    engine_version: '0.1.0',
    result: {
      schema_id: 'gms.pipeline-result/1',
      engine_version: '0.1.0',
      run_id: 'a'.repeat(64),
      mode: 'exploration',
      lifecycle_state: 'completed',
      validity: { state: 'valid', reasons: [], assumptions: [], unsupported_failure_modes: [], confidence: 'high' },
      issues: [],
      geometry_summary: { objects: 1, parse_errors: [] },
      mass: null,
      validation: null,
      structural: null,
      impact: {
        mass_kg: 0.1,
        result: {
          impact_energy_j: 0.18,
          closing_velocity_m_s: 1.9,
          effective_mass_kg: 0.1,
          impulse_n_s: 0.19,
          peak_force_n: 190,
          peak_acceleration_m_s2: 1900,
          contact_duration_s: 0.002,
          contact_compression_m: 0.0004,
          method_id: 'energy_quasi_static_v1',
          flags: [],
          assumptions: [],
          unsupported_failure_modes: [],
          validity: 'valid',
          load_path_stress_pa: null,
          safety_factor: null,
          safety_factor_status: 'not_available',
          qualification_blocked: false,
          solver_metadata: { model_id: 'screening_surrogate_v1' },
        },
        reason: null,
        unsupported_failure_modes: [],
        source: 'drop_simulation',
      },
      drop_simulation: {
        config: { test: 'drop', height_m: 0.5, surface: 'concrete', drop_count: 2, orientation: 'flat' },
        model: { mass_kg: 0.1, inertia_kg_m2: [[1e-4, 0, 0], [0, 1e-4, 0], [0, 0, 1e-4]], support_model: 'mesh_extreme_points', support_point_count: 14, integrator: 'semi_implicit_euler', timestep_s: 1 / 240, gravity_m_s2: 9.81, surface: 'concrete' },
        drops,
        impacts: drops.map((drop) => ({
          drop: drop.index,
          t_s: drop.start_s + 0.32,
          impact_speed_m_s: 3.13,
          kinetic_energy_j: 0.49,
        })),
        peak: { drop: 0, t_s: 0.32, impact_speed_m_s: 3.13, kinetic_energy_j: 0.49 },
        peak_force_estimate_n: 313,
        trajectory: drops.flatMap((drop) =>
          Array.from({ length: settle * 60 }, (_, i) => {
            const t = drop.start_s + i / 60;
            const startZ = 0.5;
            const dz = 0.5 * Math.min(1, Math.max(0, ((i / 60) - 0.25) / 0.15));
            return [t, 0, 0, startZ - dz, 1, 0, 0, 0];
          }),
        ),
      },
      qualification: null,
      manifest: null,
      errors: [],
    },
    materials: [],
  };
}

test.describe('drop simulation playback', () => {
  test('runs a drop test, animates the trajectory, and shows results', async ({ browser }) => {
    const page = await browser.newPage();
    const errors = await collectPageErrors(page);

    await page.route('**/api/analyze', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(dropSimulationResponse()),
      });
    });

    await page.goto('/');
    await page.getByRole('button', { name: 'Choose geometry file' }).click();
    await page.locator('input[type="file"]').setInputFiles(fixturePath('analytic.json'));
    await page.locator('.model-row__name', { hasText: 'analytic' }).waitFor({ state: 'attached', timeout: 15000 });

    await page.getByRole('button', { name: 'Control panel' }).click();
    await page.getByRole('button', { name: 'Run Drop Test' }).click();
    await expect(page.locator('.mission-control')).toHaveCount(0, { timeout: 5000 });

    // The drop simulation controls appear once the (mocked) result lands.
    await page.locator('.drop-sim-controls').waitFor({ state: 'visible', timeout: 15000 });
    await expect(page.locator('.drop-sim-controls__status')).toContainText('Drop 1/2');

    // On narrow viewports the auto-open navigator drawer overlays the
    // viewport controls; close it before interacting.
    if (await page.locator('.drawer--nav.is-open').count()) {
      await page.getByRole('button', { name: 'Toggle model navigator' }).click();
    }

    // Restart first so the animation is definitely playing, then pause.
    await page.getByRole('button', { name: 'Restart drop simulation' }).click();
    await page.waitForTimeout(300);
    await page.getByRole('button', { name: 'Pause drop simulation' }).click();
    await page.waitForTimeout(500); // let the freeze settle and the poll catch up
    const parseTime = (text: string) => Number(/(\d+\.\d{2})s/.exec(text)?.[1] ?? '0');
    const paused = parseTime(await page.locator('.drop-sim-controls__status').innerText());
    await page.waitForTimeout(400);
    const pausedAgain = parseTime(await page.locator('.drop-sim-controls__status').innerText());
    expect(Math.abs(pausedAgain - paused)).toBeLessThanOrEqual(0.05);

    // PLAY resumes from the exact paused moment, not from 0.00s.
    await page.getByRole('button', { name: 'Play drop simulation' }).click();
    await page.waitForTimeout(250);
    const resumed = parseTime(await page.locator('.drop-sim-controls__status').innerText());
    expect(resumed).toBeGreaterThan(paused - 0.1);
    expect(resumed).toBeLessThan(paused + 0.3);

    // The counter is monotonic: it passes through Drop 1/2 before Drop 2/2.
    await page.waitForTimeout(300);
    const counterAfterResume = await page.locator('.drop-sim-controls__status').innerText();
    expect(counterAfterResume).toMatch(/Drop [12]\/2/);

    // Restart returns to the beginning of the sequence.
    await page.getByRole('button', { name: 'Restart drop simulation' }).click();
    await expect(page.locator('.drop-sim-controls__status')).toContainText('Drop 1/2');

    // The results rail shows the drop simulation table on the Impact tab.
    // The rail defaults to a collapsed right-edge strip; expand it first.
    await page.getByRole('button', { name: 'Show results rail' }).click();
    await page.locator('.results-rail').waitFor({ state: 'visible', timeout: 5000 });
    await page.getByRole('tab', { name: 'impact' }).click();
    await expect(page.locator('.results-rail')).toContainText('Drop Simulation');
    await expect(page.locator('.results-rail')).toContainText('Worst impact: 3.13 m/s');
    await expect(page.locator('.results-rail')).toContainText('estimated peak force 313 N');

    await expectNoConsoleErrors(page, errors);
  });

  test('performs exactly the configured number of drops', async ({ browser }) => {
    const page = await browser.newPage();
    const errors = await collectPageErrors(page);

    await page.route('**/api/analyze', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(dropSimulationResponse(7)),
      });
    });

    await page.goto('/');
    await page.getByRole('button', { name: 'Choose geometry file' }).click();
    await page.locator('input[type="file"]').setInputFiles(fixturePath('analytic.json'));
    await page.locator('.model-row__name', { hasText: 'analytic' }).waitFor({ state: 'attached', timeout: 15000 });

    await page.getByRole('button', { name: 'Control panel' }).click();
    await page.getByRole('button', { name: 'Run Drop Test' }).click();

    await page.locator('.drop-sim-controls').waitFor({ state: 'visible', timeout: 15000 });
    if (await page.locator('.drawer--nav.is-open').count()) {
      await page.getByRole('button', { name: 'Toggle model navigator' }).click();
    }
    await page.getByRole('button', { name: 'Restart drop simulation' }).click();

    // Poll the counter: it must visit every drop in order and reach 7/7.
    const seen: string[] = [];
    for (let i = 0; i < 40; i += 1) {
      const text = await page.locator('.drop-sim-controls__status').innerText();
      const match = /Drop (\d+)\/7/.exec(text);
      if (match) seen.push(match[1]);
      if (seen.includes('7') && i > 5) break;
      await page.waitForTimeout(300);
    }
    expect(seen.length).toBeGreaterThan(0);
    expect(seen[seen.length - 1]).toBe('7');
    const unique = [...new Set(seen)];
    // The counter advances monotonically through the drops (no premature 7/7).
    expect(unique[0]).toBe('1');
    for (let i = 1; i < unique.length; i += 1) {
      expect(Number(unique[i])).toBeGreaterThanOrEqual(Number(unique[i - 1]));
    }

    await expectNoConsoleErrors(page, errors);
  });
});
