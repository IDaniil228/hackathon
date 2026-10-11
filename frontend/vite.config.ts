import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

export default defineConfig({
  plugins: [react()],
  server: {
    fs: { allow: [resolve("..")] },
    proxy: {
      "/api": {
        target: process.env.API_TARGET || "http://127.0.0.1:8000",
        rewrite: (path) => path.replace(/^\/api/, ""),
        cookiePathRewrite: { "/auth": "/api/auth" },
      },
    },
  },
  build: { chunkSizeWarningLimit: 700 },
});
