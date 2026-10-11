import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";
import { readFileSync } from "node:fs";

export default defineConfig(({ mode }) => ({
  plugins: [
    react(),
    {
      name: "docs-domain",
      generateBundle() {
        if (mode === "docs")
          this.emitFile({
            type: "asset",
            fileName: "CNAME",
            source: readFileSync(
              new URL("./docs.CNAME", import.meta.url),
              "utf8",
            ),
          });
      },
    },
  ],
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
  build: {
    chunkSizeWarningLimit: 700,
    outDir: mode === "docs" ? "dist-docs" : "dist",
  },
}));
