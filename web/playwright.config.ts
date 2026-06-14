import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || "",
    trace: "on-first-retry",
  },
});
