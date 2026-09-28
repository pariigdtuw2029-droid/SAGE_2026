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
        const tier = risk >= 65 ? "High-Risk" : risk >= 21.8 ? "Borderline" : "Space-Safe";
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

  const NAV = [
    { section: "Console Overview", items: [
      { idx: "01", label: "Burn-In Ingestion", href: "burn-in.html" },
    ]},
    { section: "Screening & Inspection", items: [
      { idx: "02", label: "Lot Overview", href: "lot-analytics.html" },
      { idx: "03", label: "Component Inspector", href: "component-detail.html" },
      { idx: "04", label: "Component Database", href: "components.html" },
    ]},
    { section: "Platform & Governance", items: [
      { idx: "05", label: "Inspection Reports", href: "reports.html" },
      { idx: "06", label: "System Diagnostics", href: "settings.html" },
    ]},
  ];

  function renderSidebar(active) {
    const el = document.getElementById("sidebar");
    if (!el) return;
    const here = location.pathname.split("/").pop();
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
      <div class="sidebar-foot">SAGE console · model sage-1.1</div>
    `;
  }

  /* ---------------- chrome helpers ---------------- */
  function topbar(title, backHref, backLabel, actionsHtml) {
    const el = document.getElementById("consoleTop");
    if (!el) return;
    let authPill = "";
    if (window.SAGEAPI && typeof window.SAGEAPI.isAuthenticated === "function") {
      const isAuth = window.SAGEAPI.isAuthenticated();
      const user = window.SAGEAPI.getUser();
      const username = (user && user.username) ? user.username : "Engineer";
      const role = (user && user.role) ? user.role.toUpperCase() : "ENGINEER";
      if (isAuth) {
        authPill = `<div class="auth-pill" style="display:inline-flex;align-items:center;gap:8px;padding:5px 12px;background:rgba(52,211,153,0.08);border:1px solid rgba(52,211,153,0.3);border-radius:20px;font-family:var(--mono);font-size:11px;color:var(--safe);"><span style="width:6px;height:6px;border-radius:50%;background:var(--safe);box-shadow:0 0 6px var(--safe);"></span><span>${username}</span><span style="font-size:9.5px;padding:1px 6px;border-radius:4px;background:rgba(52,211,153,0.18);letter-spacing:0.06em;font-weight:600;">${role}</span><button type="button" onclick="if(window.SAGEAPI){window.SAGEAPI.logout();window.location.href='/pages/login.html';}" style="background:transparent;border:none;color:var(--steel);cursor:pointer;padding:0 2px;font-family:inherit;font-size:11px;" title="Sign Out">✕</button></div>`;
      } else {
        authPill = `<a href="login.html" class="auth-pill" style="display:inline-flex;align-items:center;gap:6px;padding:5px 12px;background:rgba(143,201,250,0.08);border:1px solid rgba(143,201,250,0.22);border-radius:20px;font-family:var(--mono);font-size:11px;color:var(--sky);text-decoration:none;transition:background 0.2s;"><span>Sign In</span></a>`;
      }
    }
    el.innerHTML = `
      ${backHref ? `<a class="backlink" href="${backHref}">← ${backLabel || "Back"}</a>` : "<span></span>"}
      <div class="console-title">${title}</div>
      <div class="console-actions">${actionsHtml || ""}${authPill}</div>
    `;
  }

  function toast(msg, duration = 4000) {
    let t = document.querySelector(".toast");
    if (!t) {
      t = document.createElement("div");
      t.className = "toast";
      document.body.appendChild(t);
    }
    t.textContent = msg;
    requestAnimationFrame(() => t.classList.add("show"));
    clearTimeout(t._h);
    t._h = setTimeout(() => t.classList.remove("show"), duration);
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

  /* ---------------- canvas charts (interactive, zero-dep) ---------------- */
  let _tooltipEl = null;
  function getTooltipEl() {
    if (!_tooltipEl || !document.body.contains(_tooltipEl)) {
      _tooltipEl = document.getElementById("sage-chart-tooltip");
      if (!_tooltipEl) {
        _tooltipEl = document.createElement("div");
        _tooltipEl.id = "sage-chart-tooltip";
        _tooltipEl.className = "sage-chart-tooltip";
        document.body.appendChild(_tooltipEl);
      }
    }
    return _tooltipEl;
  }

  function showTooltip(html, clientX, clientY) {
    const el = getTooltipEl();
    el.innerHTML = html;
    const pad = 14;
    const rect = el.getBoundingClientRect();
    let x = clientX;
    let y = clientY;
    if (x + rect.width / 2 > window.innerWidth - pad) {
      x = window.innerWidth - rect.width / 2 - pad;
    } else if (x - rect.width / 2 < pad) {
      x = rect.width / 2 + pad;
    }
    el.style.left = `${x}px`;
    el.style.top = `${y}px`;
    el.classList.add("show");
  }

  function hideTooltip() {
    if (_tooltipEl) {
      _tooltipEl.classList.remove("show");
    }
  }

  function prep(canvas, height) {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = canvas.clientWidth || (canvas.parentElement ? canvas.parentElement.clientWidth : 300);
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
    const pad = { l: 44, r: 16, t: 16, b: 26 };
    const all = series.flatMap((s) => s.points || []);
    if (!all.length) return;

    const nonLimitSeries = series.filter((s) => !/safety|limit/i.test(s.label || ""));
    const telemetryPts = (nonLimitSeries.length ? nonLimitSeries : series).flatMap((s) => s.points || []);
    let xs = [...new Set(all.map((p) => p.x))].sort((a, b) => a - b);
    let telemYs = telemetryPts.map((p) => p.y);
    let telemMin = telemYs.length ? Math.min(...telemYs) : 0;
    let telemMax = telemYs.length ? Math.max(...telemYs) : 1;

    let y0 = opts.yMin != null ? opts.yMin : telemMin;
    let y1 = opts.yMax != null ? opts.yMax : telemMax;

    if (opts.conformalInterval) {
      if (opts.conformalInterval.low != null && opts.conformalInterval.low < y0) y0 = opts.conformalInterval.low;
      if (opts.conformalInterval.high != null && opts.conformalInterval.high > y1) y1 = opts.conformalInterval.high;
    }

    const limitSeries = series.find((s) => /safety|limit/i.test(s.label || ""));
    const safetyLimitVal = limitSeries && limitSeries.points && limitSeries.points[0] ? limitSeries.points[0].y : null;
    let safetyLimitOffScale = false;
    if (safetyLimitVal != null && opts.yMax == null) {
      if (safetyLimitVal <= (y1 * 1.8)) {
        if (safetyLimitVal > y1) y1 = safetyLimitVal;
      } else {
        safetyLimitOffScale = true;
      }
    }

    const span = y1 - y0 || 1;
    y0 -= span * 0.08;
    y1 += span * 0.08;

    function render(hovered) {
      const { ctx, w, h } = prep(canvas, H);
      const X = (x) => pad.l + (x - xs[0]) / (xs[xs.length - 1] - xs[0] || 1) * (w - pad.l - pad.r);
      const Y = (y) => pad.t + (1 - (Math.min(Math.max(y, y0), y1) - y0) / (y1 - y0)) * (h - pad.t - pad.b);

      // Grid lines
      ctx.strokeStyle = "rgba(147,166,184,0.18)";
      ctx.fillStyle = "#5c6c7c";
      ctx.font = "10px 'IBM Plex Mono', monospace";
      ctx.lineWidth = 1;
      for (let i = 0; i <= 4; i++) {
        const gy = pad.t + (i / 4) * (h - pad.t - pad.b);
        const gv = Math.round((y1 - (i / 4) * (y1 - y0)) * 10) / 10;
        ctx.beginPath(); ctx.moveTo(pad.l, gy); ctx.lineTo(w - pad.r, gy); ctx.stroke();
        ctx.fillText(String(gv), 6, gy + 3);
      }
      for (const x of xs) {
        ctx.fillText(opts.xTick ? opts.xTick(x) : String(x), X(x) - 10, h - 8);
      }

      if (safetyLimitOffScale && safetyLimitVal != null) {
        ctx.save();
        ctx.fillStyle = "rgba(248, 113, 113, 0.75)";
        ctx.font = "10px 'IBM Plex Mono', monospace";
        ctx.textAlign = "right";
        ctx.fillText(`Datasheet Limit: ${safetyLimitVal} µA (Safe Headroom > 80%)`, w - pad.r, pad.t - 4);
        ctx.restore();
      }

      // Conformal prediction interval bracket if supplied
      if (opts.conformalInterval && opts.conformalInterval.low != null && opts.conformalInterval.high != null) {
        const ci = opts.conformalInterval;
        const cx = X(ci.x);
        const cyLow = Y(ci.low);
        const cyHigh = Y(ci.high);

        ctx.save();
        const bandW = 20;
        const gCi = ctx.createLinearGradient(0, cyHigh, 0, cyLow);
        gCi.addColorStop(0, "rgba(201,168,106,0.16)");
        gCi.addColorStop(1, "rgba(201,168,106,0.04)");
        ctx.fillStyle = gCi;
        ctx.fillRect(cx - bandW / 2, cyHigh, bandW, cyLow - cyHigh);

        ctx.strokeStyle = "rgba(201,168,106,0.85)";
        ctx.lineWidth = 1.5;
        ctx.setLineDash([3, 2]);
        ctx.beginPath();
        ctx.moveTo(cx, cyLow);
        ctx.lineTo(cx, cyHigh);
        ctx.stroke();
        ctx.setLineDash([]);

        ctx.beginPath();
        ctx.moveTo(cx - 7, cyLow); ctx.lineTo(cx + 7, cyLow);
        ctx.moveTo(cx - 7, cyHigh); ctx.lineTo(cx + 7, cyHigh);
        ctx.stroke();
        ctx.restore();
      }

      // Series lines
      for (const s of series) {
        if (!s.points || !s.points.length) continue;

        // Glow underlay pass
        ctx.save();
        ctx.strokeStyle = s.color;
        ctx.globalAlpha = 0.35;
        ctx.lineWidth = 5;
        ctx.filter = "blur(6px)";
        ctx.beginPath();
        s.points.forEach((p, i) => i ? ctx.lineTo(X(p.x), Y(p.y)) : ctx.moveTo(X(p.x), Y(p.y)));
        ctx.stroke();
        ctx.restore();

        // Crisp pass
        ctx.strokeStyle = s.color;
        ctx.lineWidth = 1.8;
        if (s.dashed) ctx.setLineDash([5, 4]); else ctx.setLineDash([]);
        ctx.beginPath();
        s.points.forEach((p, i) => i ? ctx.lineTo(X(p.x), Y(p.y)) : ctx.moveTo(X(p.x), Y(p.y)));
        ctx.stroke();
        ctx.setLineDash([]);

        // Soft area fill for single-series charts
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

        // Point dots
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
            ctx.strokeStyle = s.color;
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.arc(X(p.x), Y(p.y), 6, 0, Math.PI * 2);
            ctx.stroke();
          }
        }
      }

      // Hover Crosshair & Point Highlight
      if (hovered && hovered.p) {
        const hp = hovered.p;
        const hs = hovered.s;
        const hx = X(hp.x);
        const hy = Y(hp.y);

        ctx.save();
        ctx.strokeStyle = "rgba(143,201,250,0.45)";
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 3]);
        ctx.beginPath();
        ctx.moveTo(hx, pad.t);
        ctx.lineTo(hx, h - pad.b);
        ctx.stroke();
        ctx.setLineDash([]);

        ctx.shadowColor = hs.color || "#8fc9fa";
        ctx.shadowBlur = 14;
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(hx, hy, 7, 0, Math.PI * 2);
        ctx.stroke();

        ctx.fillStyle = hs.color || "#8fc9fa";
        ctx.beginPath();
        ctx.arc(hx, hy, 4, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
      }

      return { X, Y, w, h };
    }

    render(null);

    canvas._lineChartData = { series, opts, xs, y0, y1, pad, H, render };
    if (!canvas._lineChartBound) {
      canvas._lineChartBound = true;
      canvas.addEventListener("pointermove", (e) => {
        const d = canvas._lineChartData;
        if (!d) return;
        const rect = canvas.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const my = e.clientY - rect.top;

        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        const w = canvas.width / dpr;
        const h = d.H;
        const X = (x) => d.pad.l + (x - d.xs[0]) / (d.xs[d.xs.length - 1] - d.xs[0] || 1) * (w - d.pad.l - d.pad.r);
        const Y = (y) => d.pad.t + (1 - (y - d.y0) / (d.y1 - d.y0)) * (h - d.pad.t - d.pad.b);

        let best = null, bestDist = Infinity;
        for (const s of d.series) {
          for (const p of s.points || []) {
            const px = X(p.x);
            const py = Y(p.y);
            const dist = Math.hypot(mx - px, my - py);
            if (dist < bestDist) {
              bestDist = dist;
              best = { s, p, px, py };
            }
          }
        }

        if (best && bestDist < 45) {
          d.render(best);
          const xTick = d.opts.xTick ? d.opts.xTick(best.p.x) : `${best.p.x}h`;
          const yVal = Number(best.p.y).toFixed(3);
          const yUnit = d.opts.yUnit || (best.s.label && best.s.label.includes("µA") ? "µA" : "");
          const tag = best.p.forecast
            ? '<span class="badge monitor" style="font-size:9px;padding:2px 6px;">FORECAST</span>'
            : '<span class="badge safe" style="font-size:9px;padding:2px 6px;">MEASURED</span>';

          const html = `
            <div class="tt-title">
              <span>${best.s.label || "Telemetry Checkpoint"}</span>
              ${tag}
            </div>
            <div class="tt-row">
              <span class="tt-label">Burn-In Checkpoint:</span>
              <span class="tt-val">${xTick}</span>
            </div>
            <div class="tt-row">
              <span class="tt-label">Recorded Value:</span>
              <span class="tt-val" style="color:var(--sky);">${yVal} ${yUnit}</span>
            </div>
          `;
          showTooltip(html, e.clientX, e.clientY);
        } else {
          d.render(null);
          hideTooltip();
        }
      });

      canvas.addEventListener("pointerleave", () => {
        const d = canvas._lineChartData;
        if (d && d.render) d.render(null);
        hideTooltip();
      });
    }
  }

  function hbarChart(canvas, rows, opts = {}) {
    const H = opts.height || rows.length * 34 + 16;
    const labelW = opts.labelW || 118, valW = 52;
    const max = Math.max(...rows.map((r) => r.value), 1);

    function render(hoveredIdx) {
      const { ctx, w, h } = prep(canvas, H);
      rows.forEach((r, i) => {
        const y = 12 + i * 34;
        const isHov = hoveredIdx === i;

        ctx.font = isHov ? "600 12px 'IBM Plex Mono', monospace" : "11.5px 'IBM Plex Mono', monospace";
        ctx.fillStyle = isHov ? "#ffffff" : "#93a6b8";
        ctx.fillText(r.label, 0, y + 12);

        const bx = labelW, bw = w - labelW - valW;
        ctx.fillStyle = isHov ? "rgba(147,166,184,0.18)" : "rgba(147,166,184,0.1)";
        ctx.beginPath(); ctx.roundRect(bx, y, bw, 15, 7); ctx.fill();

        const segW = Math.max(3, (r.value / max) * bw);
        const g = ctx.createLinearGradient(bx, 0, bx + segW, 0);
        g.addColorStop(0, r.color + "cc");
        g.addColorStop(1, r.color);
        ctx.fillStyle = g;
        ctx.save();
        ctx.shadowColor = r.color;
        ctx.shadowBlur = isHov ? 16 : 9;
        ctx.beginPath(); ctx.roundRect(bx, y, segW, 15, 7); ctx.fill();

        if (isHov) {
          ctx.strokeStyle = "#ffffff";
          ctx.lineWidth = 1;
          ctx.stroke();
        }
        ctx.restore();

        ctx.fillStyle = "#eef3f7";
        ctx.fillText(String(r.value), w - valW + 8, y + 12);
      });
    }

    render(-1);

    canvas._hbarData = { rows, opts, H, max, render };
    if (!canvas._hbarBound) {
      canvas._hbarBound = true;
      canvas.addEventListener("pointermove", (e) => {
        const d = canvas._hbarData;
        if (!d) return;
        const rect = canvas.getBoundingClientRect();
        const my = e.clientY - rect.top;

        if (my >= 8 && my <= 12 + d.rows.length * 34) {
          const idx = Math.floor((my - 8) / 34);
          if (idx >= 0 && idx < d.rows.length) {
            d.render(idx);
            const r = d.rows[idx];
            const pct = ((r.value / d.max) * 100).toFixed(0);
            const html = `
              <div class="tt-title">
                <span class="dot" style="background:${r.color};width:8px;height:8px;"></span>
                <span>${r.label}</span>
              </div>
              <div class="tt-row">
                <span class="tt-label">Metric Value:</span>
                <span class="tt-val">${r.value}</span>
              </div>
              <div class="tt-row">
                <span class="tt-label">Relative Magnitude:</span>
                <span class="tt-val" style="color:var(--sky);">${pct}% of peak</span>
              </div>
            `;
            showTooltip(html, e.clientX, e.clientY);
            return;
          }
        }
        d.render(-1);
        hideTooltip();
      });

      canvas.addEventListener("pointerleave", () => {
        const d = canvas._hbarData;
        if (d && d.render) d.render(-1);
        hideTooltip();
      });
    }
  }

  function donutChart(canvas, slices, opts = {}) {
    const H = opts.height || 200;
    const total = slices.reduce((a, s) => a + s.value, 0) || 1;

    function render(hoveredIdx) {
      const { ctx, w, h } = prep(canvas, H);
      const cx = w / 2, cy = h / 2;
      const R = Math.min(w, h) / 2 - 12;
      const r = R * 0.62;
      let a0 = -Math.PI / 2;

      ctx.save();
      for (let i = 0; i < slices.length; i++) {
        const s = slices[i];
        const a1 = a0 + (s.value / total) * Math.PI * 2;
        const isHov = hoveredIdx === i;
        const curR = isHov ? R + 4 : R;
        const curr = isHov ? r - 2 : r;

        ctx.beginPath();
        ctx.arc(cx, cy, curR, a0, a1);
        ctx.arc(cx, cy, curr, a1, a0, true);
        ctx.closePath();
        ctx.fillStyle = s.color;
        ctx.shadowColor = s.color;
        ctx.shadowBlur = isHov ? 22 : 14;
        ctx.fill();

        if (isHov) {
          ctx.strokeStyle = "#ffffff";
          ctx.lineWidth = 1.5;
          ctx.stroke();
        }
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

    render(-1);

    canvas._donutData = { slices, opts, H, total, render };
    if (!canvas._donutBound) {
      canvas._donutBound = true;
      canvas.addEventListener("pointermove", (e) => {
        const d = canvas._donutData;
        if (!d) return;
        const rect = canvas.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const my = e.clientY - rect.top;

        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        const w = canvas.width / dpr;
        const h = d.H;
        const cx = w / 2, cy = h / 2;
        const R = Math.min(w, h) / 2 - 12;
        const r = R * 0.62;

        const dist = Math.hypot(mx - cx, my - cy);
        if (dist >= r - 4 && dist <= R + 8) {
          let angle = Math.atan2(my - cy, mx - cx);
          if (angle < -Math.PI / 2) angle += Math.PI * 2;

          let a0 = -Math.PI / 2;
          let foundIdx = -1;
          for (let i = 0; i < d.slices.length; i++) {
            const a1 = a0 + (d.slices[i].value / d.total) * Math.PI * 2;
            if (angle >= a0 && angle < a1) {
              foundIdx = i;
              break;
            }
            a0 = a1;
          }

          if (foundIdx >= 0) {
            d.render(foundIdx);
            const sl = d.slices[foundIdx];
            const pct = ((sl.value / d.total) * 100).toFixed(1);
            const html = `
              <div class="tt-title">
                <span class="dot" style="background:${sl.color};width:8px;height:8px;"></span>
                <span>${sl.label}</span>
              </div>
              <div class="tt-row">
                <span class="tt-label">Component Count:</span>
                <span class="tt-val">${sl.value}</span>
              </div>
              <div class="tt-row">
                <span class="tt-label">Proportion:</span>
                <span class="tt-val" style="color:var(--sky);">${pct}%</span>
              </div>
            `;
            showTooltip(html, e.clientX, e.clientY);
            return;
          }
        }
        d.render(-1);
        hideTooltip();
      });

      canvas.addEventListener("pointerleave", () => {
        const d = canvas._donutData;
        if (d && d.render) d.render(-1);
        hideTooltip();
      });
    }
  }

  function scatterChart(canvas, points, opts = {}) {
    const H = opts.height || 260;
    const pad = { l: 40, r: 14, t: 14, b: 30 };
    const x0 = 0, x1 = 100, y0 = 0, y1 = 100;

    function render(hoveredPoint) {
      const { ctx, w, h } = prep(canvas, H);
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
        ctx.fillText(`threshold (${opts.threshold})`, w - pad.r - 80, Y(opts.threshold) - 5);
        ctx.fillText(`threshold (${opts.threshold})`, pad.l + 4, Y(opts.threshold) - 5);
      }

      for (const p of points) {
        const isHov = hoveredPoint === p;
        ctx.save();
        ctx.shadowColor = p.color;
        ctx.shadowBlur = isHov ? 14 : 6;
        ctx.beginPath();
        ctx.fillStyle = p.color;
        ctx.globalAlpha = isHov ? 1.0 : 0.88;
        const rad = isHov ? (p.r || 2.6) + 3.2 : (p.r || 2.6);
        ctx.arc(X(p.x), Y(p.y), rad, 0, Math.PI * 2);
        ctx.fill();

        if (isHov) {
          ctx.strokeStyle = "#ffffff";
          ctx.lineWidth = 1.8;
          ctx.stroke();
        }
        ctx.restore();
      }

      if (opts.axisLabels) {
        ctx.fillStyle = "#93a6b8";
        ctx.fillText(opts.axisLabels.x, w - pad.r - 110, h - 10);
        ctx.save();
        ctx.translate(10, pad.t + 30); ctx.rotate(-Math.PI / 2);
        ctx.fillText(opts.axisLabels.y, 0, 0);
        ctx.restore();
      }

      return { X, Y };
    }

    render(null);

    canvas._scatterData = { points, opts, pad, H, render };
    if (!canvas._scatterBound) {
      canvas._scatterBound = true;
      canvas.addEventListener("pointermove", (e) => {
        const d = canvas._scatterData;
        if (!d) return;
        const rect = canvas.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const my = e.clientY - rect.top;

        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        const w = canvas.width / dpr;
        const h = d.H;
        const X = (x) => d.pad.l + (x - x0) / (x1 - x0) * (w - d.pad.l - d.pad.r);
        const Y = (y) => d.pad.t + (1 - (y - y0) / (y1 - y0)) * (h - d.pad.t - d.pad.b);

        let best = null, bestDist = Infinity;
        for (const p of d.points) {
          const px = X(p.x);
          const py = Y(p.y);
          const dist = Math.hypot(mx - px, my - py);
          if (dist < bestDist) {
            bestDist = dist;
            best = p;
          }
        }

        if (best && bestDist < 18) {
          d.render(best);
          const title = best.id ? `Component ${best.id}` : "Plotted Point";
          const decBadge = best.decision ? badge(best.decision) : "";
          const xLbl = d.opts.axisLabels && d.opts.axisLabels.x ? d.opts.axisLabels.x.split("(")[0].trim() : "X";
          const yLbl = d.opts.axisLabels && d.opts.axisLabels.y ? d.opts.axisLabels.y.split("(")[0].trim() : "Y";

          const html = `
            <div class="tt-title">
              <span>${title}</span>
              ${decBadge}
            </div>
            <div class="tt-row">
              <span class="tt-label">${xLbl}:</span>
              <span class="tt-val">${Number(best.x).toFixed(1)}</span>
            </div>
            <div class="tt-row">
              <span class="tt-label">${yLbl}:</span>
              <span class="tt-val">${Number(best.y).toFixed(1)}</span>
            </div>
            ${best.risk != null ? `<div class="tt-row"><span class="tt-label">Risk Score:</span><span class="tt-val" style="color:var(--gold);">${Number(best.risk).toFixed(1)}</span></div>` : ""}
          `;
          showTooltip(html, e.clientX, e.clientY);
        } else {
          d.render(null);
          hideTooltip();
        }
      });

      canvas.addEventListener("pointerleave", () => {
        const d = canvas._scatterData;
        if (d && d.render) d.render(null);
        hideTooltip();
      });
    }
  }

  const COLORS = {
    safe: "#34d399", monitor: "#fbbf24", hold: "#fb923c", reject: "#f87171",
    sky: "#8fc9fa", steel: "#93a6b8", gold: "#c9a86a", violet: "#a78bfa", teal: "#4fd1c5",
  };

  /* ---------------- export ---------------- */
  window.SAGE = {
    data: api, COLORS,
    renderSidebar, topbar, toast, param, dotFor, badge,
    charts: { lineChart, hbarChart, donutChart, scatterChart },
    api: window.SAGEAPI || null,
  };
  if (window.SAGEAPI && !window.SAGEAPI.sage) {
    window.SAGEAPI.sage = window.SAGE;
  }
})();
