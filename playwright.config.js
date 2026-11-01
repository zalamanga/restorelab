import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/browser',
  testMatch: process.env.RESTORELAB_CAPTURE === '1' ? '**/screenshots.spec.js' : '**/workflows.spec.js',
  outputDir: process.env.RESTORELAB_CAPTURE === '1' ? 'test-results/capture' : 'test-results/browser',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: Boolean(process.env.CI),
  timeout: 240000,
  expect: { timeout: 15000 },
  use: {
    baseURL: process.env.RESTORELAB_URL || 'http://127.0.0.1:8140',
    channel: process.env.PLAYWRIGHT_CHANNEL || (process.env.CI ? 'chromium' : 'msedge'),
    trace: 'off', video: 'off', screenshot: 'only-on-failure',
    actionTimeout: 20000, navigationTimeout: 45000,
  },
  projects: [{name:'desktop',use:{...devices['Desktop Chrome'],viewport:{width:1440,height:1040}}}],
  reporter: [['list'], ['html',{open:'never'}]],
});
