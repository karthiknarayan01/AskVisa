import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In development the app runs on Vite's server and sends /api to askvisa-server on port 8000.
// In production askvisa-server serves the built files itself, so /api is the same origin.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: true } },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom"],
          markdown: ["react-markdown", "remark-gfm"],
          motion: ["motion"],
        },
      },
    },
  },
  test: { environment: "jsdom" },
});
