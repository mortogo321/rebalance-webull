import { defineConfig } from "vitest/config";

// Must be set here, in the main process, before defineConfig runs: Vitest's
// thread-based worker pools inherit process.env but not the main process's
// resolved zone, so setting TZ via `test.env` has no effect on Date/Intl
// inside the workers that actually run the tests.
process.env.TZ = "UTC";

export default defineConfig({
  test: {
    environment: "node",
    // Mirrors the "@/*" -> "./*" path mapping in tsconfig.json.
    alias: {
      "@/": new URL("./", import.meta.url).pathname,
    },
  },
});
