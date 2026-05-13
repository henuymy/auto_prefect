import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          validation: ["ajv", "zod", "@hookform/resolvers", "react-hook-form"],
          ui: ["lucide-react", "react-resizable-panels", "sonner"],
          monaco: ["@monaco-editor/react", "monaco-editor"],
        },
      },
    },
  },
});
