import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "../awsflow/static",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      // In dev, forward all /api/* requests to the uvicorn backend
      "/api": "http://localhost:8000",
    },
  },
});
