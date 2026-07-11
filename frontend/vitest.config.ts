import { defineConfig } from "vitest/config";

// Minimal Vitest config: jsdom for anything that touches Image /
// DOM, and only the ``src/**`` glob so accidental co-located
// ``.test.ts`` files elsewhere don't get picked up.
export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    globals: false,
  },
});
