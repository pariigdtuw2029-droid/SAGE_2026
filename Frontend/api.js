/**
 * ASTRA-GUARD — Centralized Frontend API Client
 * Smart India Hackathon 2026 | Team BINARY BADDIES
 *
 * Connects the ASTRA-GUARD console to the FastAPI backend at http://127.0.0.1:8000
 * Handles:
 *  1. POST /api/burnin/upload             - Upload burn-in test CSV
 *  2. GET  /api/lots                      - List all lots
 *  3. GET  /api/lots/{lot_id}             - Single lot details
 *  4. GET  /api/lots/{lot_id}/summary     - Aggregated lot summary stats
 *  5. GET  /api/components/{component_id} - Component screening snapshot
 *  6. GET  /api/components/{id}/trajectory- Actual vs predicted burn-in trajectory
 *  7. GET  /api/alerts                    - High-risk component alerts
 *  8. GET  /api/components/{id}/report    - Detailed component engineering report
 *  9. GET  /health                        - Backend health check
 */
(function (global) {
  "use strict";

  function getBaseUrl() {
    try {
      const stored = localStorage.getItem("ASTRA_GUARD_API_URL");
      if (stored && stored.trim()) return stored.trim().replace(/\/+$/, "");
    } catch (_) {}
    if (global.__API_BASE_URL__) return String(global.__API_BASE_URL__).replace(/\/+$/, "");
    // When the page itself is served from a public host (Render, etc.), the
    // API lives on the same origin — no cross-origin config needed. The
    // hardcoded localhost default only applies when opening files locally.
    if (typeof location !== "undefined" && location.hostname
        && !/^(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|.+\.local)$/.test(location.hostname)) {
      return "";
    }
    return "http://127.0.0.1:8000";
  }

  function setBaseUrl(url) {
    if (!url) return;
    const clean = url.trim().replace(/\/+$/, "");
    try {
      localStorage.setItem("ASTRA_GUARD_API_URL", clean);
    } catch (_) {}
  }

  async function request(path, options = {}) {
    const base = getBaseUrl();
    const url = `${base}${path.startsWith("/") ? "" : "/"}${path}`;
    const timeout = options.timeout || 8000;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);

    const fetchOptions = {
      method: options.method || "GET",
      signal: controller.signal,
      ...options,
    };

    // For non-FormData requests, set Accept and JSON headers
    if (!(options.body instanceof FormData)) {
      fetchOptions.headers = {
        Accept: "application/json",
        ...(options.headers || {}),
      };
      if (options.body && typeof options.body === "object" && !(options.body instanceof Blob)) {
        fetchOptions.headers["Content-Type"] = "application/json";
        fetchOptions.body = JSON.stringify(options.body);
      }
    }

    try {
      const res = await fetch(url, fetchOptions);
      clearTimeout(timer);

      let payload = null;
      const ctype = res.headers.get("content-type") || "";
      if (ctype.includes("application/json")) {
        try {
          payload = await res.json();
        } catch (_) {
          payload = null;
        }
      } else {
        payload = await res.text();
      }

      if (!res.ok) {
        const errorDetail = (payload && payload.detail) ? payload.detail : `HTTP error ${res.status}`;
        return { ok: false, status: res.status, error: errorDetail, data: null };
      }

      return { ok: true, status: res.status, data: payload, error: null };
    } catch (err) {
      clearTimeout(timer);
      const isAbort = err.name === "AbortError";
      const msg = isAbort
        ? "Request timed out. Backend service may be offline or unresponsive."
        : "Cannot connect to backend server at " + base;
      return { ok: false, status: 0, error: msg, data: null };
    }
  }

  const client = {
    getBaseUrl,
    setBaseUrl,

    // 1. POST /api/burnin/upload
    async uploadBurnIn(file) {
      if (!file) return { ok: false, error: "No file selected." };
      const form = new FormData();
      form.append("file", file);
      return request("/api/burnin/upload", {
        method: "POST",
        body: form,
      });
    },

    // 2. GET /api/lots
    async getLots() {
      return request("/api/lots");
    },

    // 3. GET /api/lots/{lot_id}
    async getLot(lotId) {
      if (!lotId) return { ok: false, error: "Missing lot ID." };
      return request(`/api/lots/${encodeURIComponent(lotId)}`);
    },

    // 4. GET /api/lots/{lot_id}/summary
    async getLotSummary(lotId) {
      if (!lotId) return { ok: false, error: "Missing lot ID." };
      return request(`/api/lots/${encodeURIComponent(lotId)}/summary`);
    },

    // 5. GET /api/components/{component_id}
    async getComponent(componentId) {
      if (!componentId) return { ok: false, error: "Missing component ID." };
      return request(`/api/components/${encodeURIComponent(componentId)}`);
    },

    // 6. GET /api/components/{component_id}/trajectory
    async getComponentTrajectory(componentId) {
      if (!componentId) return { ok: false, error: "Missing component ID." };
      return request(`/api/components/${encodeURIComponent(componentId)}/trajectory`);
    },

    // 7. GET /api/alerts
    async getAlerts() {
      return request("/api/alerts");
    },

    // 8. GET /api/components/{component_id}/report
    async getComponentReport(componentId) {
      if (!componentId) return { ok: false, error: "Missing component ID." };
      return request(`/api/components/${encodeURIComponent(componentId)}/report`);
    },

    // 9. GET /health
    async checkHealth() {
      return request("/health", { timeout: 3000 });
    },

    // 10. GET /api/lots/{lot_id}/components
    async getLotComponents(lotId) {
      if (!lotId) return { ok: false, error: "Missing lot ID." };
      return request(`/api/lots/${encodeURIComponent(lotId)}/components`);
    },
  };

  global.ASTRAGuardAPI = client;
  if (global.SAGE) {
    global.SAGE.api = client;
  }
})(typeof window !== "undefined" ? window : this);
