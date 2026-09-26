/* SAGE console — shared runtime for the dashboard pages.
   Pages live in pages/; the landing page is ../index.html relative to them.
   All data is mock, but the headline numbers mirror the real Member-2
   backend run (20 lots / 9,562 components / 438 rejected / C1739 = 99.4). */
(() => {
  "use strict";

  /* ---------------- deterministic RNG ---------------- */
  function mulberry32(seed) {
    let a = seed >>> 0;
    return function () {
      a |= 0; a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  const rand = mulberry32(2026);
  const pick = (arr) => arr[Math.floor(rand() * arr.length)];
  const rnum = (lo, hi, dp = 1) => {
    const v = lo + rand() * (hi - lo);
    const f = Math.pow(10, dp);
    return Math.round(v * f) / f;
  };

  /* ---------------- mock dataset ---------------- */
  const LOT_PROFILES = [
    { temp: 125, label: "Thermal-High" },
    { temp: 85,  label: "Thermal-Med" },
    { temp: -40, label: "Cold-Spare" },
    { temp: 65,  label: "Thermal-Low" },
    { temp: 25,  label: "Ambient" },
  ];
  const TYPES = ["MOSFET", "Op-Amp", "Voltage Regulator"];

  const lots = [];
  const components = [];

  (function buildDataset() {
    // 20 lots whose sizes sum to exactly 9,562 (matches the real ingest).
    const sizes = Array(20).fill(478);
    sizes[6] += 1; sizes[12] += 1; // 478*20 = 9,560 → 9,562
    let cid = 0;
    for (let i = 0; i < 20; i++) {
      const lotId = `LOT-${String(i + 1).padStart(2, "0")}`;
      const prof = LOT_PROFILES[i % LOT_PROFILES.length];
      const type = TYPES[i % TYPES.length];
      const lot = {
        lotId, type, temp: prof.temp, tempLabel: prof.label,
        size: sizes[i], pass: 0, monitor: 0, hold: 0, reject: 0,
      };
      for (let k = 0; k < sizes[i]; k++) {
        cid += 1;
        const id = `C${String(cid).padStart(4, "0")}`;
        const u = rand();
        let decision, risk;
        if (u < 0.715)      { decision = "PASS";    risk = rnum(4, 34, 1); }
        else if (u < 0.89)  { decision = "MONITOR"; risk = rnum(34, 64, 1); }
        else if (u < 0.955) { decision = "HOLD";    risk = rnum(64, 84, 1); }
        else                { decision = "REJECT";  risk = rnum(84, 99.6, 1); }

        const anomaly = Math.min(99.5, Math.max(1.2, risk + rnum(-18, 12, 1)));
        const drift   = Math.min(99.5, Math.max(1.0, risk + rnum(-14, 14, 1)));
        const unc     = Math.min(99.0, Math.max(2.0, rnum(5, 45, 1)));
        const tier = risk >= 65 ? "High-Risk" : risk >= 35 ? "Borderline" : "Space-Safe";
        const rel = Math.max(0.2, Math.min(99.8, 100 - risk - rnum(0, 6, 1)));

        components.push({
          id, lotId, type,
          risk, anomaly, drift, uncertainty: unc,
          decision, tier, reliabilityIndex: Math.round(rel * 10) / 10,
          confidence: rnum(0.62, 0.97, 2),
          measurements: makeMeasurements(risk),
          explanations: makeExplanations(id, decision, risk),
        });
        lot[decision === "PASS" ? "pass" : decision === "MONITOR" ? "monitor" : decision === "HOLD" ? "hold" : "reject"] += 1;
      }
      lots.push(lot);
    }

    // Force the headline trio from the real pipeline output.
    pin("C1739", 99.4, "REJECT", "Oxide degradation");
    pin("C2156", 94.2, "REJECT", "Radiation drift");
    pin("C0892", 78.5, "HOLD",   "Interconnect fatigue");

    // Nudge HOLD/REJECT counts so rejects land on exactly 438.
    let rej = components.filter((c) => c.decision === "REJECT");
    const byRisk = (d) => components.filter((c) => c.decision === d).sort((a, b) => a.risk - b.risk);
    while (rej.length > 438) {
      const c = rej.shift();
      c.decision = "HOLD"; c.tier = "High-Risk";
    }
    while (rej.length < 438) {
      const c = byRisk("HOLD").pop();
      if (!c) break;
      c.decision = "REJECT";
      rej.push(c);
    }
    recount();

    function pin(id, risk, decision, reason) {
      const c = components.find((x) => x.id === id);
      if (!c) return;
      c.risk = risk; c.decision = decision; c.tier = risk >= 65 ? "High-Risk" : "Borderline";
      c.anomaly = Math.round((risk - 4.2) * 10) / 10;
      c.drift = Math.round((risk - 11) * 10) / 10;
      c.uncertainty = 32.0;
      c.confidence = decision === "REJECT" ? 0.87 : 0.81;
      c.reliabilityIndex = Math.round((100 - risk) * 10) / 10;
      c.explanations = [
        `Leakage moved +18.5% in first 24h (${reason.toLowerCase()})`,
        `Batch-relative outlier (z=4.32 vs own lot)`,
        decision === "REJECT" ? "Failed traditional fixed-limit spec check" : "Holding for extended thermal screening",
        "Forecast shows continued drift to 168h",
      ];
    }
    function recount() {
      for (const l of lots) l.pass = l.monitor = l.hold = l.reject = 0;
      for (const c of components) {
        const l = lots.find((x) => x.lotId === c.lotId);
        if (c.decision === "PASS") l.pass++;
        else if (c.decision === "MONITOR") l.monitor++;
        else if (c.decision === "HOLD") l.hold++;
        else l.reject++;
      }
    }

    function makeMeasurements(risk) {
      const sev = risk / 100;
      const leak0 = rnum(0.8, 1.6, 2), res0 = rnum(96, 104, 1), vth0 = rnum(0.62, 0.78, 2);
      const pts = (b0, dpk, div) => [0, 24, 96].map((h) => {
        const t = h / 168;
        return Math.round((b0 + dpk * sev * Math.pow(t, 0.8)) * div) / div;
      });
      const leak = pts(leak0, 0.55 * (0.4 + sev), 100);
      const res  = pts(res0, -6.5 * (0.3 + sev), 10);
      const vth  = pts(vth0, -0.09 * (0.4 + sev), 1000);
      leak.push(Math.round((leak[2] + 0.22 * (0.4 + sev)) * 100) / 100);
      res.push(Math.round((res[2] - 4.1 * (0.3 + sev)) * 10) / 10);
      vth.push(Math.round((vth[2] - 0.055 * (0.4 + sev)) * 1000) / 1000);
      return {
        hours: [0, 24, 96, 168],
        leakage: leak, resistance: res, vth,
        predictedFrom: 3, // index where the 168h point is a forecast
      };
    }
    function makeExplanations(id, decision, risk) {
      const base = [
        `Leakage moved +${rnum(4, 19, 1)}% in first 24h (oxide degradation)`,
      ];
      if (risk >= 35) base.push(`Batch-relative outlier (z=${rnum(2.1, 4.4, 2)} vs own lot)`);
      if (decision === "REJECT") base.push("Failed traditional fixed-limit spec check");
      if (risk >= 65) base.push("Forecast shows continued drift to 168h");
      if (decision === "PASS") base.push("All readings within lot-normal envelope", "Stable forecast to 168h");
      return base;
    }
  })();

  /* ---------------- public data API ---------------- */
  const api = {
    lots,
    components,
    lotById: (id) => lots.find((l) => l.lotId === id),
    compsByLot: (lotId) => components.filter((c) => c.lotId === lotId),
    compById: (id) => components.find((c) => c.id === id),
    totals() {
      const t = { lots: lots.length, comps: components.length, pass: 0, monitor: 0, hold: 0, reject: 0 };
      for (const l of lots) { t.pass += l.pass; t.monitor += l.monitor; t.hold += l.hold; t.reject += l.reject; }
      return t;
    },
    topRisk(n = 10) {
      return [...components].sort((a, b) => b.risk - a.risk).slice(0, n);
    },
    reason(c) {
      return (c.explanations && c.explanations[0]) || "—";
    },
  };

  /* ---------------- sidebar ---------------- */
  const NAV = [
    { section: "Monitoring", items: [
      { idx: "01", label: "Burn-In Testing",   href: "burn-in.html" },
      { idx: "02", label: "Anomaly Detection", href: "anomaly.html" },
      { idx: "03", label: "Trends & Parameters", href: "trends.html" },
    ]},
    { section: "Analysis", items: [
      { idx: "04", label: "Component Database", href: "components.html" },
      { idx: "05", label: "Lot Analytics",      href: "lot-analytics.html" },
      { idx: "06", label: "AI Predictions",     href: "predictions.html" },
    ]},
    { section: "Output", items: [
      { idx: "07", label: "Reports",  href: "reports.html" },
      { idx: "08", label: "Settings", href: "settings.html" },
    ]},
  ];

  function renderSidebar(active) {
    const el = document.getElementById("sidebar");
    if (!el) return;
    const here = location.pathname.split("/").pop();
    const authed = window.SAGEAuth && window.SAGEAuth.isLoggedIn();
    el.innerHTML = `
      <a class="sidebar-brand" href="../index.html">
        <img src="../assets/brand/logo.png" alt="" />
        <span>SAGE</span>
      </a>
      ${NAV.map((g) => `
        <div class="sidebar-section">${g.section}</div>
        <nav class="sidebar-nav">
          ${g.items.map((it) => `
            <a href="${it.href}" class="${it.href === here || (active && active === it.href) ? "active" : ""}">
              <span class="idx">${it.idx}</span>${it.label}
            </a>`).join("")}
        </nav>`).join("")}
      <div class="sidebar-foot">SAGE console · model sage-1.0</div>
      ${authed ? `<a href="#" class="sidebar-logout" id="sidebarLogout">Sign out — ${window.SAGEAuth.getUsername() || "admin"} · ${window.SAGEAuth.getRole() || "admin"}</a>` : ""}
    `;
    const logoutLink = document.getElementById("sidebarLogout");
    if (logoutLink) {
      logoutLink.addEventListener("click", (e) => {
        e.preventDefault();
        window.SAGEAuth.logout();
      });
    }
  }

  /* ---------------- chrome helpers ---------------- */
  function topbar(title, backHref, backLabel, actionsHtml) {
    const el = document.getElementById("consoleTop");
    if (!el) return;
    el.innerHTML = `
      ${backHref ? `<a class="backlink" href="${backHref}">← ${backLabel || "Back"}</a>` : "<span></span>"}
      <div class="console-title">${title}</div>
      <div class="console-actions">${actionsHtml || ""}</div>
    `;
  }

  function toast(msg) {
    let t = document.querySelector(".toast");
    if (!t) {
      t = document.createElement("div");
      t.className = "toast";
      document.body.appendChild(t);
    }
    t.textContent = msg;
    requestAnimationFrame(() => t.classList.add("show"));
    clearTimeout(t._h);
    t._h = setTimeout(() => t.classList.remove("show"), 2600);
  }

  function param(name) {
    return new URLSearchParams(location.search).get(name);
  }

  function dotFor(decision) {
    const map = { PASS: "safe", MONITOR: "monitor", HOLD: "hold", REJECT: "reject" };
    return map[decision] || "neutral";
  }

  function badge(decision) {
    return `<span class="badge ${decision.toLowerCase()}">${decision}</span>`;
  }

  /* ---------------- canvas charts (no deps) ---------------- */
  function prep(canvas, height) {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = canvas.clientWidth || canvas.parentElement.clientWidth;
    canvas.width = w * dpr;
    canvas.height = height * dpr;
    canvas.style.height = height + "px";
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, height);
    return { ctx, w, h: height };
  }

  function lineChart(canvas, series, opts = {}) {
    const H = opts.height || 220;
    const { ctx, w, h } = prep(canvas, H);
    const pad = { l: 44, r: 14, t: 14, b: 26 };
    const all = series.flatMap((s) => s.points);
    let xs = [...new Set(all.map((p) => p.x))].sort((a, b) => a - b);
    let ys = all.map((p) => p.y);
    let y0 = opts.yMin != null ? opts.yMin : Math.min(...ys);
    let y1 = opts.yMax != null ? opts.yMax : Math.max(...ys);
    const span = y1 - y0 || 1; y0 -= span * 0.08; y1 += span * 0.08;
    const X = (x) => pad.l + (x - xs[0]) / (xs[xs.length - 1] - xs[0] || 1) * (w - pad.l - pad.r);
    const Y = (y) => pad.t + (1 - (y - y0) / (y1 - y0)) * (h - pad.t - pad.b);

    ctx.strokeStyle = "rgba(147,166,184,0.25)";
    ctx.fillStyle = "#5c6c7c";
    ctx.font = "10px 'IBM Plex Mono', monospace";
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const gy = pad.t + (i / 4) * (h - pad.t - pad.b);
      const gv = Math.round((y1 - (i / 4) * (y1 - y0)) * 10) / 10;
      ctx.beginPath(); ctx.moveTo(pad.l, gy); ctx.lineTo(w - pad.r, gy); ctx.stroke();
      ctx.fillText(String(gv), 6, gy + 3);
    }
    for (const x of xs) ctx.fillText(opts.xTick ? opts.xTick(x) : String(x), X(x) - 10, h - 8);

    for (const s of series) {
      // glow underlay pass
      ctx.save();
      ctx.strokeStyle = s.color;
      ctx.globalAlpha = 0.35;
      ctx.lineWidth = 5;
      ctx.filter = "blur(6px)";
      ctx.beginPath();
      s.points.forEach((p, i) => i ? ctx.lineTo(X(p.x), Y(p.y)) : ctx.moveTo(X(p.x), Y(p.y)));
      ctx.stroke();
      ctx.restore();

      // crisp pass
      ctx.strokeStyle = s.color;
      ctx.lineWidth = 1.8;
      if (s.dashed) ctx.setLineDash([5, 4]); else ctx.setLineDash([]);
      ctx.beginPath();
      s.points.forEach((p, i) => i ? ctx.lineTo(X(p.x), Y(p.y)) : ctx.moveTo(X(p.x), Y(p.y)));
      ctx.stroke();
      ctx.setLineDash([]);

      // soft area fill for single-series charts
      if (series.length === 1 && opts.fill !== false) {
        const g = ctx.createLinearGradient(0, pad.t, 0, h - pad.b);
        g.addColorStop(0, s.color + "33");
        g.addColorStop(1, s.color + "00");
        ctx.fillStyle = g;
        ctx.beginPath();
        s.points.forEach((p, i) => i ? ctx.lineTo(X(p.x), Y(p.y)) : ctx.moveTo(X(p.x), Y(p.y)));
        ctx.lineTo(X(s.points[s.points.length - 1].x), h - pad.b);
        ctx.lineTo(X(s.points[0].x), h - pad.b);
        ctx.closePath();
        ctx.fill();
      }

      for (const p of s.points) {
        ctx.save();
        if (p.forecast) {
          ctx.shadowColor = s.color;
          ctx.shadowBlur = 10;
        }
        ctx.beginPath();
        ctx.fillStyle = s.color;
        ctx.arc(X(p.x), Y(p.y), 3, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
        if (p.forecast) {
          ctx.strokeStyle = s.color; ctx.lineWidth = 1;
          ctx.beginPath(); ctx.arc(X(p.x), Y(p.y), 6, 0, Math.PI * 2); ctx.stroke();
        }
      }
    }
  }

  function hbarChart(canvas, rows, opts = {}) {
    const H = opts.height || rows.length * 34 + 16;
    const { ctx, w, h } = prep(canvas, H);
    const labelW = opts.labelW || 118, valW = 52;
    const max = Math.max(...rows.map((r) => r.value), 1);
    rows.forEach((r, i) => {
      const y = 12 + i * 34;
      ctx.font = "11.5px 'IBM Plex Mono', monospace";
      ctx.fillStyle = "#93a6b8";
      ctx.fillText(r.label, 0, y + 12);
      const bx = labelW, bw = w - labelW - valW;
      ctx.fillStyle = "rgba(147,166,184,0.1)";
      ctx.beginPath(); ctx.roundRect(bx, y, bw, 15, 7); ctx.fill();
      const segW = Math.max(3, (r.value / max) * bw);
      const g = ctx.createLinearGradient(bx, 0, bx + segW, 0);
      g.addColorStop(0, r.color + "cc");
      g.addColorStop(1, r.color);
      ctx.fillStyle = g;
      ctx.save();
      ctx.shadowColor = r.color;
      ctx.shadowBlur = 9;
      ctx.beginPath(); ctx.roundRect(bx, y, segW, 15, 7); ctx.fill();
      ctx.restore();
      ctx.fillStyle = "#eef3f7";
      ctx.fillText(String(r.value), w - valW + 8, y + 12);
    });
  }

  function donutChart(canvas, slices, opts = {}) {
    const H = opts.height || 200;
    const { ctx, w, h } = prep(canvas, H);
    const total = slices.reduce((a, s) => a + s.value, 0) || 1;
    const cx = w / 2, cy = h / 2, R = Math.min(w, h) / 2 - 12, r = R * 0.62;
    let a0 = -Math.PI / 2;
    ctx.save();
    ctx.shadowBlur = 0;
    for (const s of slices) {
      const a1 = a0 + (s.value / total) * Math.PI * 2;
      ctx.beginPath();
      ctx.arc(cx, cy, R, a0, a1);
      ctx.arc(cx, cy, r, a1, a0, true);
      ctx.closePath();
      ctx.fillStyle = s.color;
      ctx.shadowColor = s.color;
      ctx.shadowBlur = 14;
      ctx.fill();
      a0 = a1;
    }
    ctx.restore();
    if (opts.center) {
      ctx.fillStyle = "#eef3f7";
      ctx.font = "600 20px 'IBM Plex Mono', monospace";
      ctx.textAlign = "center";
      ctx.fillText(opts.center, cx, cy + 2);
      ctx.fillStyle = "#5c6c7c";
      ctx.font = "10px 'IBM Plex Mono', monospace";
      if (opts.centerSub) ctx.fillText(opts.centerSub, cx, cy + 18);
      ctx.textAlign = "left";
    }
  }

  function scatterChart(canvas, points, opts = {}) {
    const H = opts.height || 260;
    const { ctx, w, h } = prep(canvas, H);
    const pad = { l: 40, r: 14, t: 14, b: 30 };
    const x0 = 0, x1 = 100, y0 = 0, y1 = 100;
    const X = (x) => pad.l + (x - x0) / (x1 - x0) * (w - pad.l - pad.r);
    const Y = (y) => pad.t + (1 - (y - y0) / (y1 - y0)) * (h - pad.t - pad.b);
    ctx.strokeStyle = "rgba(147,166,184,0.16)";
    ctx.fillStyle = "#5c6c7c";
    ctx.font = "10px 'IBM Plex Mono', monospace";
    for (let i = 0; i <= 4; i++) {
      const gy = pad.t + (i / 4) * (h - pad.t - pad.b);
      ctx.beginPath(); ctx.moveTo(pad.l, gy); ctx.lineTo(w - pad.r, gy); ctx.stroke();
      ctx.fillText(String(Math.round(100 - i * 25)), 12, gy + 3);
      const gx = pad.l + (i / 4) * (w - pad.l - pad.r);
      ctx.beginPath(); ctx.moveTo(gx, pad.t); ctx.lineTo(gx, h - pad.b); ctx.stroke();
      ctx.fillText(String(i * 25), gx - 6, h - 10);
    }
    if (opts.threshold != null) {
      ctx.strokeStyle = "rgba(248,113,113,0.65)";
      ctx.setLineDash([6, 4]);
      ctx.beginPath(); ctx.moveTo(pad.l, Y(opts.threshold)); ctx.lineTo(w - pad.r, Y(opts.threshold)); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = "rgba(248,113,113,0.8)";
      ctx.fillText("threshold", w - pad.r - 58, Y(opts.threshold) - 5);
      ctx.fillText("threshold", pad.l + 4, Y(opts.threshold) - 5);
    }
    for (const p of points) {
      ctx.save();
      ctx.shadowColor = p.color;
      ctx.shadowBlur = 6;
      ctx.beginPath();
      ctx.fillStyle = p.color;
      ctx.globalAlpha = 0.88;
      ctx.arc(X(p.x), Y(p.y), p.r || 2.6, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }
    if (opts.axisLabels) {
      ctx.fillStyle = "#93a6b8";
      ctx.fillText(opts.axisLabels.x, w - pad.r - 70, h - 10);
      ctx.save();
      ctx.translate(10, pad.t + 30); ctx.rotate(-Math.PI / 2);
      ctx.fillText(opts.axisLabels.y, 0, 0);
      ctx.restore();
    }
  }

  const COLORS = {
    safe: "#34d399", monitor: "#fbbf24", hold: "#fb923c", reject: "#f87171",
    sky: "#8fc9fa", steel: "#93a6b8", gold: "#c9a86a", violet: "#a78bfa", teal: "#4fd1c5",
  };

  /* ---------------- export ---------------- */
  // Gate console pages behind the login screen when not authenticated.
  // Runs before page scripts read data so the UI never renders unauthed.
  if (window.SAGEAuth) window.SAGEAuth.requireLogin();

  window.SAGE = {
    data: api, COLORS,
    renderSidebar, topbar, toast, param, dotFor, badge,
    charts: { lineChart, hbarChart, donutChart, scatterChart },
    api: window.SAGEAPI || null,
  };
  if (window.SAGEAPI && !window.SAGEAPI.sage) {
    window.SAGEAPI.sage = window.SAGE;
  }

  // Re-run the gate in case app.js loaded before auth.js finished patching.
  if (window.SAGEAuth) window.SAGEAuth.requireLogin();
})();
