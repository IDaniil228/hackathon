import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 180000,
  expect: { timeout: 15000 },
  forbidOnly: true,
  retries: 0,
  workers: 1,
  reporter: "list",
  webServer: process.env.SCEPKA_BASE_URL
    ? undefined
    : {
        command: "npm run dev",
        url: "http://localhost:3000",
        reuseExistingServer: !process.env.CI,
        timeout: 60000,
      },
  use: {
    actionTimeout: 15000,
    baseURL: process.env.SCEPKA_BASE_URL || "http://localhost:3000",
    locale: "ru-RU",
    timezoneId: "Europe/Moscow",
    viewport: { width: 1440, height: 1000 },
    launchOptions: {
      executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
    },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "ui", testMatch: "ui.spec.ts" },
    {
      name: "live",
      testMatch: ["live.spec.ts", "candidate.spec.ts", "hiring.spec.ts"],
    },
  ],
});
