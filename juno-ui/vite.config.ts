import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: "http://127.0.0.1:5400", changeOrigin: true, configure(proxy) { proxy.on("proxyReq", req => req.setHeader("Origin", "http://127.0.0.1:5400")); } },
      "/scenarios.json": "http://127.0.0.1:5400",
    },
  },
  preview: { host: "127.0.0.1" },
});
