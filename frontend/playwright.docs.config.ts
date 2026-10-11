import { defineConfig } from "@playwright/test";
const base = process.env.DOCS_BASE_PATH || "/hackathon/";

export default defineConfig({
  testDir: "./tests",
  testMatch: "docs.spec.ts",
  forbidOnly: true,
  retries: 0,
  timeout: 60000,
  expect: { timeout: 15000 },
  reporter: "list",
  webServer: {
    command: `npm run preview:docs -- --base ${base} --port 3002`,
    url: `http://localhost:3002${base}`,
    reuseExistingServer: false,
  },
  use: {
    baseURL: "http://localhost:3002",
    launchOptions: {
      executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
    },
    screenshot: "only-on-failure",
  },
});
