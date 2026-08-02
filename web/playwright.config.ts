import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: [['list']],
  timeout: 60_000,
  use: {
    baseURL: 'http://127.0.0.1:5199',
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: 'cd .. && PYTHONPATH=. python3 -S -m mouse_sim serve --host 127.0.0.1 --port 8899 --project-root . --cache-dir .e2e-cache --quiet',
      url: 'http://127.0.0.1:8899/api/health',
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: 'npx vite preview --host 127.0.0.1 --port 5199 --strictPort',
      url: 'http://127.0.0.1:5199',
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: 'cd .. && PYTHONPATH=. python3 -S -m mouse_sim serve --host 127.0.0.1 --port 8898 --project-root . --web-dist web/dist --quiet',
      url: 'http://127.0.0.1:8898/api/health',
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
  projects: [
    { name: 'chromium-desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 }, launchOptions: { args: ['--enable-unsafe-swiftshader'] } } },
    { name: 'chromium-tablet', use: { ...devices['Desktop Chrome'], viewport: { width: 1024, height: 768 }, launchOptions: { args: ['--enable-unsafe-swiftshader'] } } },
    { name: 'chromium-mobile', use: { ...devices['Pixel 5'], launchOptions: { args: ['--enable-unsafe-swiftshader'] } } },
    { name: 'firefox-desktop', use: { ...devices['Desktop Firefox'], viewport: { width: 1280, height: 800 } } },
  ],
});
