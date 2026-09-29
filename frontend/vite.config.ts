/// <reference types="vitest/config" />
import { svelte } from "@sveltejs/vite-plugin-svelte";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";
import { defineConfig } from "vite";

// The built app goes to runway/static/app/, which the Python server serves at /.
// `npm run dev` serves it on its own port and passes API calls (and Runway's own files) through to Runway on 8765.
export default defineConfig({
  base: "/",
  plugins: [tailwindcss(), svelte()],
  resolve: { alias: { $lib: path.resolve("./src/lib") } },
  build: { outDir: "../runway/static/app", emptyOutDir: true },
  // `npm test` (Vitest) runs in one time zone, so date tests read the same everywhere. It's one behind UTC, where
  // the evening is already tomorrow: the case the date helpers are there for.
  test: {
    env: { TZ: "America/New_York" },
    // `npm run coverage`: how much of the web app the tests run, components included (so the number is honest about
    // what's untested). CI turns coverage/coverage-summary.json into the README's frontend badge.
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,svelte}"],
      exclude: ["src/**/*.test.ts", "src/**/*.d.ts", "src/main.ts"],
      reporter: ["text-summary", "json-summary"],
    },
  },
  server: {
    proxy: Object.fromEntries(["/api", "/auth", "/banks", "/logos", "/fonts", "/logo.svg", "/logo-180.png", "/sw.js", "/manifest.webmanifest", "/icon-192.png"].map((p) => [p, "http://127.0.0.1:8765"])),
  },
});
