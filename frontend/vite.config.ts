import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig(({ mode }) => {
  const isDashboard = mode === "dashboard";
  const blockedPaths = new Set(
    isDashboard ? ["/index.html"] : ["/dashboard.html"],
  );

  return {
  plugins: [
    react(),
    {
      name: "separate-frontend-entry-points",
      configureServer(server) {
        server.middlewares.use((req, res, next) => {
          const pathname = new URL(req.url ?? "/", "http://127.0.0.1").pathname;
          if (isDashboard && pathname === "/") {
            req.url = "/dashboard.html";
            next();
            return;
          }

          if (!blockedPaths.has(pathname)) {
            next();
            return;
          }

          res.statusCode = 404;
          res.end("Not Found");
        });
      },
    },
  ],
  server: {
    allowedHosts: ["yumingyang.top"],
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        ws: true,
      },
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    rollupOptions: {
      input: {
        dashboard: path.resolve(__dirname, "dashboard.html"),
        monitor: path.resolve(__dirname, "monitor.html"),
      },
      output: {
        manualChunks: {
          validation: ["ajv", "zod", "@hookform/resolvers", "react-hook-form"],
          ui: ["lucide-react", "react-resizable-panels", "sonner"],
          monaco: ["@monaco-editor/react", "monaco-editor"],
        },
      },
    },
  },
  };
});
