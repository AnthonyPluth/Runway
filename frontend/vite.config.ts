import { svelte } from "@sveltejs/vite-plugin-svelte";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";
import { defineConfig } from "vite";

// The built app goes to runway/static/next/, which the Python server serves at /next/.
// `npm run dev` serves it on its own port and passes API calls through to Runway on 8765.
export default defineConfig({
  base: "/next/",
  plugins: [tailwindcss(), svelte()],
  resolve: { alias: { $lib: path.resolve("./src/lib") } },
  build: { outDir: "../runway/static/next", emptyOutDir: true },
  server: {
    proxy: Object.fromEntries(["/api", "/auth", "/banks", "/logos", "/fonts", "/logo.svg"].map((p) => [p, "http://127.0.0.1:8765"])),
  },
});
