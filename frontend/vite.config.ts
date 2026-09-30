/// <reference types="vitest/config" />
import { codecovVitePlugin } from "@codecov/vite-plugin";
import { sentryVitePlugin } from "@sentry/vite-plugin";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import tailwindcss from "@tailwindcss/vite";
import { svelteTesting } from "@testing-library/svelte/vite";
import path from "node:path";
import { defineConfig } from "vite";

// Readable stack traces in Sentry: with SENTRY_AUTH_TOKEN (the release build; see the Dockerfile), the build makes
// source maps, uploads them to Sentry for the release (RUNWAY_VERSION) and deletes them, so they're never served.
// Without it (every other build) there are no source maps and nothing is uploaded.
const sentryToken = process.env.SENTRY_AUTH_TOKEN || undefined;

// The built app goes to runway/static/app/, which the Python server serves at /.
// `npm run dev` serves it on its own port and passes API calls (and Runway's own files) through to Runway on 8765.
export default defineConfig({
  base: "/",
  // svelteTesting() makes Svelte resolve to its browser build under Vitest and unmounts components after each test.
  // codecovVitePlugin() sends the build's bundle sizes to Codecov, which tracks them and reports the change on pull
  // requests. Only in CI, where CODECOV_TOKEN is set; elsewhere it does nothing.
  plugins: [
    tailwindcss(),
    svelte(),
    svelteTesting(),
    codecovVitePlugin({
      enableBundleAnalysis: Boolean(process.env.CODECOV_TOKEN),
      bundleName: "runway-web",
      uploadToken: process.env.CODECOV_TOKEN,
      telemetry: false,
    }),
    sentryVitePlugin({
      disable: !sentryToken,
      authToken: sentryToken,
      org: process.env.SENTRY_ORG,
      project: process.env.SENTRY_BROWSER_PROJECT || "runway-web",
      telemetry: false,
      // The release itself (its commits, when it's done) is made by the release workflow, for the server's project too.
      release: { name: process.env.RUNWAY_VERSION || undefined, create: false, finalize: false, setCommits: false },
      sourcemaps: { filesToDeleteAfterUpload: ["../runway/static/app/**/*.map"] },
      bundleSizeOptimizations: { excludeDebugStatements: true, excludeReplayWorker: true },   // the worker isn't used (monitoring.ts)
    }),
  ],
  resolve: { alias: { $lib: path.resolve("./src/lib") } },
  build: { outDir: "../runway/static/app", emptyOutDir: true, sourcemap: sentryToken ? "hidden" : false },
  // `npm test` (Vitest) runs in one time zone, so date tests read the same everywhere. It's one behind UTC, where
  // the evening is already tomorrow: the case the date helpers are there for.
  test: {
    env: { TZ: "America/New_York" },
    // Logic tests run in Node (fast). A component test opts into the DOM with a `// @vitest-environment jsdom` first line.
    setupFiles: ["src/test/setup.ts"],
    // `npm run coverage`: how much of the web app the tests run, components included (so the number is honest about
    // what's untested). CI sends coverage/lcov.info to Codecov (the README's frontend badge).
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,svelte}"],
      exclude: ["src/**/*.test.ts", "src/**/*.d.ts", "src/main.ts", "src/test/**"],
      reporter: ["text-summary", "lcovonly"],
    },
  },
  server: {
    proxy: Object.fromEntries(["/api", "/auth", "/banks", "/logos", "/fonts", "/logo.svg", "/logo-180.png", "/sw.js", "/manifest.webmanifest", "/icon-192.png"].map((p) => [p, "http://127.0.0.1:8765"])),
  },
});
