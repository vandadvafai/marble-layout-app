import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite config kept intentionally small. The proxy below means dev
// requests to `/api/*` from the frontend land on the FastAPI server
// without needing CORS in production builds. Local dev still allows
// CORS via the backend's middleware so direct fetch() also works.
//
// IMPORTANT: target uses 127.0.0.1, NOT "localhost".
// On macOS + Node 18+, "localhost" resolves to ::1 (IPv6) first via
// dns.lookup's default. uvicorn defaults to binding 127.0.0.1 only
// (IPv4), so a Node proxy hop via "localhost" fails with
// ECONNREFUSED ::1:8000 even though `curl http://localhost:8000`
// works (curl falls back IPv4→IPv6, Node doesn't). Pinning the
// proxy target to 127.0.0.1 is the smallest fix that makes the
// loopback paths agree.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
