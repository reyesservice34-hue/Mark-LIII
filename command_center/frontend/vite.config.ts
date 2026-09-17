import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

// Built assets land in backend/static so the FastAPI app serves them directly.
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  build: {
    outDir: "../backend/static",
    emptyOutDir: true,
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ["react", "react-dom", "react-router-dom"],
          markdown: ["marked", "dompurify", "highlight.js/lib/core"],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8080", changeOrigin: false, ws: true },
      "/v1": { target: "http://localhost:8080", changeOrigin: false },
    },
  },
});
