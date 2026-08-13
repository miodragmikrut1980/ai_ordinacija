// @ts-check
const { defineConfig, devices } = require('@playwright/test');

/**
 * E2E config for clinic-ai-assistant.
 *
 * Why this exists: the API test suite (backend/tests, pytest) is thorough
 * but never actually drives a browser -- it can't catch a broken selector,
 * a JS error that silently no-ops a button, a CSS regression that hides a
 * control, or a real login->MFA->dashboard flow the way a lekar
 * experiences it. This fills that gap.
 *
 * IMPORTANT for whoever runs this: these tests were written and reviewed
 * for correctness against the actual HTML/JS (exact element ids, form
 * field names, dialog structure), but could NOT be executed in the
 * environment that wrote them -- that sandbox has no outbound access to
 * cdn.playwright.dev, so the Chromium binary Playwright needs could never
 * be downloaded there. They are expected to run cleanly in CI (see
 * .github/workflows/tests.yml's `e2e` job) or on a developer machine with
 * normal internet access, but the first real run should be watched
 * closely rather than assumed correct from a design review alone.
 */
module.exports = defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: false, // tests share one demo clinic's data -- see e2e/README.md
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://127.0.0.1:8899',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  // Starts the real app (demo mode: seeded org/users, disposable SQLite in
  // a temp dir) and waits for /api/health before running any test.
  webServer: process.env.E2E_BASE_URL ? undefined : {
    command: 'bash e2e/start-server.sh',
    url: 'http://127.0.0.1:8899/api/health',
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
});
