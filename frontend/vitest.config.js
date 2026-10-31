// Tests run in jsdom (a simulated browser), so modules that touch the DOM can be imported.
//   npm test
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: { environment: "jsdom", include: ["tests/**/*.test.js"], setupFiles: ["tests/setup.js"] },
});
