/// <reference types="vitest/config" />
import { svelte } from "@sveltejs/vite-plugin-svelte";
import tailwindcss from "@tailwindcss/vite";
import { svelteTesting } from "@testing-library/svelte/vite";
import path from "node:path";
import { defineConfig } from "vite";

// The built app goes to runway/static/app/, which the Python server serves at /.
// `npm run dev` serves it on its own port and passes API calls (and Runway's own files) through to Runway on 8765.
export default defineConfig({
  base: "/",
  // svelteTesting() makes Svelte resolve to its browser build under Vitest and unmounts components after each test.
  plugins: [tailwindcss(), svelte(), svelteTesting()],
  resolve: { alias: { $lib: path.resolve("./src/lib") } },
  build: { outDir: "../runway/static/app", emptyOutDir: true },
  // `npm test` (Vitest) runs in one time zone, so date tests read the same everywhere. It's one behind UTC, where
  // the evening is already tomorrow: the case the date helpers are there for.
  test: {
    env: { TZ: "America/New_York" },
    // Logic tests run in Node (fast). A component test opts into the DOM with a `// @vitest-environment jsdom` first line.
    setupFiles: ["src/test/setup.ts"],
    // `npm run coverage`: how much of the web app the tests run, components included (so the number is honest about
    // what's untested). CI turns coverage/coverage-summary.json into the README's frontend badge.
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,svelte}"],
      exclude: ["src/**/*.test.ts", "src/**/*.d.ts", "src/main.ts", "src/test/**"],
      reporter: ["text-summary", "json-summary"],
    },
  },
  server: {
    proxy: Object.fromEntries(["/api", "/auth", "/banks", "/logos", "/fonts", "/logo.svg", "/logo-180.png", "/sw.js", "/manifest.webmanifest", "/icon-192.png"].map((p) => [p, "http://127.0.0.1:8765"])),
  },
});
