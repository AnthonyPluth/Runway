// Run with `npm run lint`, which lints from the repository root so this one config also covers the browser extension
// and the server's service worker: plain JavaScript with no build step or package of their own. Paths are relative
// to the repository root for that reason.
import js from "@eslint/js";
import { defineConfig } from "eslint/config";
import svelte from "eslint-plugin-svelte";
import globals from "globals";
import ts from "typescript-eslint";

const FRONTEND = "frontend/**/*.{js,ts,svelte}";

export default defineConfig(
  // The web app builds into runway/static/app.
  { ignores: ["runway/static/app/**", "**/node_modules/**"] },
  js.configs.recommended,
  ts.configs.recommended,
  // The Svelte rules only for the web app: one of them crashes on the extension's classic (non-module) scripts.
  ...svelte.configs.recommended.map((c) => (c.files ? c : { ...c, files: [FRONTEND] })),
  {
    files: [FRONTEND],
    languageOptions: { globals: { ...globals.browser } },
    rules: {
      // `{" · "}` and `{" "}` are deliberate: they keep a separator's spaces next to an {#if} that would trim them.
      "svelte/no-useless-mustaches": "off",
      // It flags every Date, Set and URLSearchParams in a Svelte file, but ours are throwaway locals or memos kept
      // non-reactive on purpose; reactive state here is replaced, not mutated in place.
      "svelte/prefer-svelte-reactivity": "off",
    },
  },
  {
    rules: {
      // A leading underscore marks a name that's unused on purpose (a page's unused route props, `catch (_)`).
      "@typescript-eslint/no-unused-vars": ["error", {
        argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrorsIgnorePattern: "^_",
      }],
    },
  },
  {
    files: ["frontend/*.{js,ts}", "frontend/verify/*.mjs"],
    languageOptions: { globals: { ...globals.node } },
  },
  {
    files: ["frontend/**/*.svelte", "frontend/**/*.svelte.ts"],
    languageOptions: { parserOptions: { parser: ts.parser } },
  },
  {
    // Classic scripts (not modules), loaded by the extension's pages and its background worker.
    files: ["extension/**/*.js"],
    languageOptions: {
      sourceType: "script",
      globals: { ...globals.browser, ...globals.serviceworker, ...globals.webextensions },
    },
  },
  {
    files: ["runway/static/**/*.js"],
    languageOptions: { sourceType: "script", globals: { ...globals.serviceworker } },
  },
);
