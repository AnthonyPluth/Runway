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
  server: {
    proxy: Object.fromEntries(["/api", "/auth", "/banks", "/logos", "/fonts", "/logo.svg", "/logo-180.png", "/sw.js", "/manifest.webmanifest", "/icon-192.png"].map((p) => [p, "http://127.0.0.1:8765"])),
  },
});
