/**
 * SAGE — Centralized Frontend API Client
 * Smart India Hackathon 2026 | Team BINARY BADDIES
 *
 * Connects the SAGE console to the FastAPI backend at http://127.0.0.1:8000
 * Handles:
 *  1. POST /api/burnin/upload             - Upload burn-in test CSV
 *  2. GET  /api/lots                      - List all lots
 *  3. GET  /api/lots/{lot_id}             - Single lot details
 *  4. GET  /api/lots/{lot_id}/summary     - Aggregated lot summary stats
 *  5. GET  /api/components/{component_id} - Component screening snapshot
 *  6. GET  /api/components/{id}/trajectory- Actual vs predicted burn-in trajectory
 *  7. GET  /api/alerts                    - High-risk component alerts
 *  8. GET  /api/components/{id}/report    - Detailed component engineering report
 *  8b.GET  /api/components/{id}/reviews   - Component engineering review history
 *  8c.POST /api/components/{id}/review    - Submit component engineering review
 *  9. GET  /health                        - Backend health check
 *  10.GET  /api/lots/{lot_id}/components  - Components in a specific lot
 *  11.GET  /api/system/status             - System diagnostics and model health
 *  12.POST /api/auth/login                - JWT engineering authentication
 *  13.GET  /api/auth/me                   - Current user identity verification
 */
(function (global) {
  "use strict";

  function getBaseUrl() {
    try {
      if (typeof localStorage !== "undefined" && localStorage.getItem("ASTRA_GUARD_API_URL")) {
        localStorage.removeItem("ASTRA_GUARD_API_URL");
      }
      const stored = localStorage.getItem("SAGE_API_URL");
      if (stored && stored.trim()) return stored.trim().replace(/\/+$/, "");
    } catch (_) {}
    if (global.__API_BASE_URL__) return String(global.__API_BASE_URL__).replace(/\/+$/, "");
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
      localStorage.setItem("SAGE_API_URL", clean);
    } catch (_) {}
  }

  const TOKEN_KEY = "SAGE_AUTH_TOKEN";
  const USER_KEY = "SAGE_AUTH_USER";

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const RETRYABLE_STATUS = new Set([0, 502, 503, 504]);
  const GET_ATTEMPTS = 4;
  const GET_BACKOFF_MS = [2000, 3500, 5000];

  function getToken() {
    try {
      return localStorage.getItem(TOKEN_KEY) || null;
    } catch (_) {
      return null;
    }
  }

  function setToken(token) {
    try {
      if (token) {
        localStorage.setItem(TOKEN_KEY, token);
      } else {
        localStorage.removeItem(TOKEN_KEY);
      }
    } catch (_) {}
  }

  function getUser() {
    try {
      const u = localStorage.getItem(USER_KEY);
      return u ? JSON.parse(u) : null;
    } catch (_) {
      return null;
    }
  }

  function setUser(user) {
    try {
      if (user) {
        localStorage.setItem(USER_KEY, JSON.stringify(user));
      } else {
        localStorage.removeItem(USER_KEY);
      }
    } catch (_) {}
  }

  function logout() {
    const token = getToken();
    if (token) {
      try {
        fetch(`${getBaseUrl()}/api/auth/logout`, {
          method: "POST",
          headers: {
            "Authorization": `Bearer ${token}`,
            "Content-Type": "application/json"
          }
        }).catch(() => {});
      } catch (_) {}
    }
    setToken(null);
    setUser(null);
  }

  function isAuthenticated() {
    return Boolean(getToken());
  }

  // Paths where a 401 is a *normal* response (bad credentials, etc.) and must
  // NOT trigger the redirect-to-login loop.
  function isAuthPath(pathname) {
    return typeof pathname === "string" && pathname.includes("/pages/login.html");
  }

  function getUserRole() {
    const u = getUser();
    return (u && u.role) ? u.role.toLowerCase() : null;
  }

  async function request(path, options = {}) {
    const base = getBaseUrl();
    const url = `${base}${path.startsWith("/") ? "" : "/"}${path}`;
    const method = options.method || "GET";
    const timeout = options.timeout || (method === "GET" ? 12000 : 30000);
    const attempts = method === "GET" ? GET_ATTEMPTS : 1;

    let lastResult = null;

    for (let attempt = 0; attempt < attempts; attempt++) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeout);

      const fetchOptions = {
        method,
        signal: controller.signal,
        ...options,
      };

      if (!(options.body instanceof FormData)) {
        fetchOptions.headers = {
          Accept: "application/json",
          ...(options.headers || {}),
        };
        if (options.body && typeof options.body === "object" && !(options.body instanceof Blob)) {
          fetchOptions.headers["Content-Type"] = "application/json";
          fetchOptions.body = JSON.stringify(options.body);
        }
      } else {
        fetchOptions.headers = {
          ...(options.headers || {}),
        };
      }

      const token = getToken();
      if (token && !fetchOptions.headers["Authorization"]) {
        fetchOptions.headers["Authorization"] = `Bearer ${token}`;
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
          if (res.status === 401) {
            // Token missing, expired, or invalid. Clear it and send the user to
            // the sign-in page — staying here only produces "Backend Connection
            // Failed" noise, since the data endpoints are auth-protected.
            logout();
            if (!isAuthPath(location.pathname)) {
              const loginUrl = (typeof location !== "undefined" && location.protocol === "file:")
                ? "login.html"
                : "/pages/login.html";
              const back = encodeURIComponent(location.pathname + location.search);
              setTimeout(() => { window.location.href = loginUrl + "?redirect=" + back; }, 400);
            }
          } else if (res.status === 403) {
            try {
              if (typeof window !== "undefined" && window.SAGE && typeof window.SAGE.toast === "function") {
                window.SAGE.toast("Access Denied (403): " + errorDetail);
              }
            } catch (_) {}
          }
          lastResult = { ok: false, status: res.status, error: errorDetail, data: null };
          if (RETRYABLE_STATUS.has(res.status) && attempt < attempts - 1) {
            await sleep(GET_BACKOFF_MS[attempt] || 5000);
            continue;
          }
          return lastResult;
        }

        return { ok: true, status: res.status, data: payload, error: null };
      } catch (err) {
        clearTimeout(timer);
        const isAbort = err.name === "AbortError";
        const msg = isAbort
          ? "Request timed out. The backend may be waking up or offline — retrying helps on free-tier cold starts."
          : "Cannot connect to the SAGE backend (network error, not an authentication problem).";
        lastResult = { ok: false, status: 0, error: msg, data: null };
        if (attempt < attempts - 1) {
          await sleep(GET_BACKOFF_MS[attempt] || 5000);
          continue;
        }
      }
    }

    return lastResult;
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

    // 8b. GET /api/components/{component_id}/reviews
    async getComponentReviews(componentId) {
      if (!componentId) return { ok: false, error: "Missing component ID." };
      return request(`/api/components/${encodeURIComponent(componentId)}/reviews`);
    },

    // 8c. POST /api/components/{component_id}/review
    async submitComponentReview(componentId, disposition, comment) {
      if (!componentId) return { ok: false, error: "Missing component ID." };
      if (!disposition) return { ok: false, error: "Missing disposition." };
      return request(`/api/components/${encodeURIComponent(componentId)}/review`, {
        method: "POST",
        body: { disposition, comment: comment || null },
      });
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

    // 11. GET /api/system/status
    async getSystemStatus() {
      return request("/api/system/status", { timeout: 5000 });
    },

    // --- Authentication ---
    getToken,
    setToken,
    getUser,
    setUser,
    getUserRole,
    isAuthenticated,
    logout,

    // 12. POST /api/auth/login
    async login(username, password) {
      if (!username || !password) {
        return { ok: false, error: "Username and password are required." };
      }
      const res = await request("/api/auth/login", {
        method: "POST",
        body: { username: username.trim(), password },
      });
      if (res.ok && res.data && res.data.access_token) {
        setToken(res.data.access_token);
        const meRes = await request("/api/auth/me");
        if (meRes.ok && meRes.data) {
          setUser(meRes.data);
        } else {
          setUser({ username: username.trim() });
        }
      }
      return res;
    },

    // 13. GET /api/auth/me
    async getCurrentUser() {
      const res = await request("/api/auth/me");
      if (res.ok && res.data) {
        setUser(res.data);
      } else if (res.status === 401) {
        logout();
      }
      return res;
    },

    // 13b. GET /api/auth/me (alias me)
    async me() {
      return this.getCurrentUser();
    },
  };

  global.SAGEAPI = client;
  if (global.SAGE) {
    global.SAGE.api = client;
  }
})(typeof window !== "undefined" ? window : this);
