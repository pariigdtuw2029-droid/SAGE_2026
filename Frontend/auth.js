/**
 * SAGE — Frontend auth layer
 * Smart India Hackathon 2026 | Team BINARY BADDIES
 *
 * JWT session handling for the console:
 *  - stores the bearer token in localStorage (SAGE_AUTH_TOKEN)
 *  - auto-attaches Authorization: Bearer <token> to every SAGEAPI request
 *  - on a 401 from any API call, clears the session and shows the login gate
 *  - SAGEAuth.requireLogin() gates every console page (pages/*.html)
 *  - SAGEAuth.logout() calls POST /api/auth/logout and returns to the gate
 *
 * The landing page (../index.html) stays public; only pages/ require auth.
 */
(function (global) {
  "use strict";

  var TOKEN_KEY = "SAGE_AUTH_TOKEN";
  var REFRESH_KEY = "SAGE_AUTH_REFRESH";
  var USER_KEY = "SAGE_AUTH_USER";
  var _refreshInFlight = null;  // dedupes concurrent 401s into one refresh call

  function safeGet(key) {
    try { return localStorage.getItem(key); } catch (_) { return null; }
  }
  function safeSet(key, value) {
    try { localStorage.setItem(key, value); } catch (_) {}
  }
  function safeRemove(key) {
    try { localStorage.removeItem(key); } catch (_) {}
  }

  var ROLE_KEY = "SAGE_AUTH_ROLE";

  function getToken() { return safeGet(TOKEN_KEY) || ""; }
  function getRefreshToken() { return safeGet(REFRESH_KEY) || ""; }
  function getUsername() { return safeGet(USER_KEY) || ""; }
  function getRole() { return safeGet(ROLE_KEY) || ""; }
  function isLoggedIn() { return !!getToken() || !!getRefreshToken(); }

  function saveSession(token, refresh, username, role) {
    if (token) safeSet(TOKEN_KEY, token);
    if (refresh) safeSet(REFRESH_KEY, refresh);
    if (username) safeSet(USER_KEY, username);
    if (role) safeSet(ROLE_KEY, role);
  }

  function clearSession() {
    safeRemove(TOKEN_KEY);
    safeRemove(REFRESH_KEY);
    safeRemove(USER_KEY);
    safeRemove(ROLE_KEY);
  }

  /* ---------------- transparent token refresh ----------------
   * When an API call 401s (access token expired) and we hold a refresh
   * token, silently POST /api/auth/refresh, swap both tokens, and replay
   * the original request once. The user never sees a re-login unless the
   * refresh token itself has expired (default: 7 days).
   */
  function rawFetch(path, options) {
    var base = (global.SAGEAPI && global.SAGEAPI.getBaseUrl()) || "";
    var url = base + (path.charAt(0) === "/" ? "" : "/") + path;
    var init = options || {};
    init.headers = Object.assign({}, init.headers || {});
    if (init.body && typeof init.body === "object" && !(init.body instanceof FormData) && !(init.body instanceof Blob)) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(init.body);
    }
    return nativeFetch(url, init);
  }
  var nativeFetch = global.fetch.bind(global);

  async function refreshAccessToken() {
    if (_refreshInFlight) return _refreshInFlight;  // one flight per burst
    _refreshInFlight = (async function () {
      var rt = getRefreshToken();
      if (!rt) return false;
      try {
        var res = await rawFetch("/api/auth/refresh", {
          method: "POST",
          headers: { "Authorization": "Bearer " + rt },
        });
        if (!res.ok) return false;
        var data = await res.json();
        if (!data || !data.access_token) return false;
        saveSession(data.access_token, data.refresh_token, data.username, data.role);
        return true;
      } catch (_) {
        return false;
      }
    })();
    var ok = await _refreshInFlight;
    _refreshInFlight = null;
    return ok;
  }

  async function fetchWithRetry(url, init) {
    var res = await nativeFetch(url, init);
    if (res.status !== 401 || !getRefreshToken()) return res;
    if (init && init.__retried) return res;
    var ok = await refreshAccessToken();
    if (!ok) return res;  // caller sees the 401 and clears the session
    var retryInit = Object.assign({}, init, { __retried: true });
    return nativeFetch(url, retryInit);
  }

  /* ---------------- login gate (injected overlay) ---------------- */

  function ensureGate() {
    var gate = document.getElementById("sageLoginGate");
    if (gate) return gate;
    gate = document.createElement("div");
    gate.id = "sageLoginGate";
    gate.innerHTML =
      '<div class="sage-login-card">' +
      '  <div class="sage-login-brand">' +
      '    <img src="../assets/brand/logo.png" alt="" onerror="this.style.display=\'none\'" />' +
      '    <span>SAGE</span>' +
      '  </div>' +
      '  <h1>Restricted console</h1>' +
      '  <p class="sage-login-sub">Sign in to access the inspection platform.</p>' +
      '  <form id="sageLoginForm">' +
      '    <label class="sage-login-label" for="sageLoginUser">Username</label>' +
      '    <input class="sage-login-input" id="sageLoginUser" name="username" autocomplete="username" required />' +
      '    <label class="sage-login-label" for="sageLoginPass">Password</label>' +
      '    <input class="sage-login-input" id="sageLoginPass" name="password" type="password" autocomplete="current-password" required />' +
      '    <button class="sage-login-btn" id="sageLoginBtn" type="submit">Sign in</button>' +
      '    <div class="sage-login-error" id="sageLoginError" hidden></div>' +
      '  </form>' +
      '</div>';
    document.body.appendChild(gate);
    return gate;
  }

  function showError(msg) {
    var el = document.getElementById("sageLoginError");
    if (!el) return;
    el.textContent = msg;
    el.hidden = false;
  }

  async function handleLoginSubmit(event, api) {
    event.preventDefault();
    var btn = document.getElementById("sageLoginBtn");
    var userEl = document.getElementById("sageLoginUser");
    var passEl = document.getElementById("sageLoginPass");
    var errEl = document.getElementById("sageLoginError");
    if (errEl) errEl.hidden = true;
    if (btn) { btn.disabled = true; btn.textContent = "Signing in…"; }

    var res = await api.login(userEl.value.trim(), passEl.value);
    if (btn) { btn.disabled = false; btn.textContent = "Sign in"; }

    if (res && res.ok && res.data && res.data.access_token) {
      saveSession(res.data.access_token, res.data.refresh_token, res.data.username, res.data.role);
      var gate = document.getElementById("sageLoginGate");
      if (gate) gate.remove();
      document.dispatchEvent(new CustomEvent("sage:login"));
      return true;
    }
    showError((res && res.error) || "Login failed — check your credentials and backend URL.");
    return false;
  }

  /**
   * Call at the top of every console page script. Returns immediately when
   * already logged in; otherwise covers the page with the login gate.
   */
  function requireLogin() {
    var api = global.SAGEAPI;
    if (!api) return;
    if (isLoggedIn()) return;
    var gate = ensureGate();
    gate.classList.add("show");
    var form = document.getElementById("sageLoginForm");
    form.addEventListener("submit", function (e) { handleLoginSubmit(e, api); });
  }

  /* ---------------- logout ---------------- */

  async function logout() {
    var api = global.SAGEAPI;
    clearSession();
    try { if (api) await api.logout(); } catch (_) {}  // best-effort; token is gone either way
    location.href = location.pathname.split("/").pop() || "index.html";
  }

  /* ---------------- wire into the API client ---------------- */

  function patchApi() {
    var api = global.SAGEAPI;
    if (!api || api.__authPatched) return;

    // Wrap every verb-bearing client method so the bearer token rides along.
    var methods = [
      "uploadBurnIn", "getLots", "getLot", "getLotSummary", "getComponent",
      "getComponentTrajectory", "getAlerts", "getComponentReport",
      "checkHealth", "getLotComponents",
    ];
    methods.forEach(function (name) {
      var original = api[name];
      if (typeof original !== "function") return;
      api[name] = function () {
        var args = Array.prototype.slice.call(arguments);
        return original.apply(api, args).then(function (res) {
          if (res && res.status === 401) {
            clearSession();
            if (typeof api.onUnauthorized === "function") api.onUnauthorized();
          }
          return res;
        });
      };
    });

    api.setToken = function (token) { saveSession(token, getUsername()); };
    api.getToken = getToken;
    api.onUnauthorized = function () {
      if (document.getElementById("sageLoginGate")) return;  // gate already up
      requireLogin();
    };
    api.__authPatched = true;
  }

  function patchRequestHeaders() {
    // Wrap window.fetch: inject the bearer token into same-API calls that
    // lack one, and transparently retry once after a successful refresh
    // when a call comes back 401 (access token expired mid-session).
    global.fetch = function (input, init) {
      try {
        var url = typeof input === "string" ? input : (input && input.url) || "";
        var isApi = /\/api\//.test(url) || /\/health$/.test(url);
        var hasAuth = !!(init && init.headers && init.headers.Authorization);
        if (isApi && !hasAuth && getToken()) {
          var headers = Object.assign({}, (init && init.headers) || {});
          headers.Authorization = "Bearer " + getToken();
          init = Object.assign({}, init || {}, { headers: headers });
        }
        if (isApi) return fetchWithRetry(url, init);
      } catch (_) {}
      return nativeFetch(input, init);
    };
  }

  patchRequestHeaders();
  if (global.SAGEAPI) patchApi();
  else document.addEventListener("DOMContentLoaded", patchApi);

  global.SAGEAuth = {
    getToken: getToken,
    getRefreshToken: getRefreshToken,
    getUsername: getUsername,
    getRole: getRole,
    isLoggedIn: isLoggedIn,
    saveSession: saveSession,
    clearSession: clearSession,
    refreshAccessToken: refreshAccessToken,
    requireLogin: requireLogin,
    logout: logout,
  };
  if (global.SAGE) global.SAGE.auth = global.SAGEAuth;
})(typeof window !== "undefined" ? window : this);
