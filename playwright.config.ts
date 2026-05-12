import { defineConfig, devices } from "@playwright/test";

// Smoke tests run against a real site_server instance on PORT 8502. Spin
// it up with `python site_server.py` (or the desktop shortcut) before
// running `npm run test:e2e`. We deliberately do NOT have Playwright
// boot the server automatically because the site server has Python deps
// that are easier to manage with the existing PowerShell launcher.

const baseURL = process.env.PLAYWRIGHT_BASE_URL || "http://localhost:8502";

export default defineConfig({
  testDir: "./tests-e2e",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL,
    headless: true,
    viewport: { width: 1280, height: 800 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "mobile-chrome",
      use: { ...devices["Pixel 5"], viewport: { width: 390, height: 844 } },
      testIgnore: ["**/play-camera.spec.ts"],
    },
    {
      name: "chromium-fake-camera",
      testMatch: ["**/play-camera.spec.ts"],
      use: {
        ...devices["Desktop Chrome"],
        launchOptions: {
          args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"],
        },
        permissions: ["camera"],
      },
    },
  ],
});
