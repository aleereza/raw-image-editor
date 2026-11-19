import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vite";

// `npm run dev` serves the UI with hot reload and sends API calls to `rie serve` on port 7317.
export default defineConfig({
  plugins: [svelte()],
  server: { proxy: { "/api": "http://127.0.0.1:7317" } },
  build: { outDir: "dist", emptyOutDir: true, target: "es2022" },
});
