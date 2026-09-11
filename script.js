(() => {
  "use strict";

  /* ----------------------------------------------------------------
     0. HERO STARFIELD — lightweight animated, twinkling, drifting
        stars rendered on canvas. Runs immediately, independent of
        the frame-sequence preloader below.
  ------------------------------------------------------------------- */
  (function initStarfield() {
    const canvas = document.getElementById("heroStars");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let w, h, dpr, stars;

    function resize() {
      const rect = canvas.getBoundingClientRect();
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = rect.width;
      h = rect.height;
      canvas.width = w * dpr;
      canvas.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      seed();
    }

    function seed() {
      const count = Math.round((w * h) / 4800);
      stars = Array.from({ length: count }, () => {
        const isBlue = Math.random() < 0.3;
        return {
          x: Math.random() * w,
          y: Math.random() * h,
          r: Math.random() * 1.3 + 0.35,
          base: Math.random() * 0.5 + 0.35,
          speed: Math.random() * 0.015 + 0.004,
          phase: Math.random() * Math.PI * 2,
          dx: (Math.random() - 0.5) * 0.045,
          dy: (Math.random() - 0.5) * 0.045,
          color: isBlue ? "143,201,250" : "255,255,255",
        };
      });
    }

    let t = 0;
    let raf = null;
    function draw() {
      t += 1;
      ctx.clearRect(0, 0, w, h);
      for (const s of stars) {
        s.x += s.dx;
        s.y += s.dy;
        if (s.x < -2) s.x = w + 2;
        if (s.x > w + 2) s.x = -2;
        if (s.y < -2) s.y = h + 2;
        if (s.y > h + 2) s.y = -2;
        const a = reduce ? s.base : s.base + Math.sin(t * s.speed + s.phase) * 0.3;
        ctx.beginPath();
        ctx.fillStyle = `rgba(${s.color},${Math.max(a, 0.05)})`;
        ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
        ctx.fill();
      }
      raf = requestAnimationFrame(draw);
    }

    resize();
    draw();
    window.addEventListener("resize", () => {
      cancelAnimationFrame(raf);
      resize();
      draw();
    });
  })();

  /* ----------------------------------------------------------------
     1. SEQUENCE CONFIG — actual frame counts discovered in the
        supplied ZIP. Order preserved exactly as extracted.
  ------------------------------------------------------------------- */
  const SEQUENCES = {
    rocket:    { folder: "assets/rocket",    count: 240 },
    satellite: { folder: "assets/satellite", count: 241 },
    component: { folder: "assets/component", count: 241 },
  };

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function framePath(seq, index) {
    const n = String(index).padStart(3, "0");
    return `${SEQUENCES[seq].folder}/frame-${n}.jpg`;
  }

  /* ----------------------------------------------------------------
     2. PRELOAD every frame of every sequence before the experience
        is interactive, so scrubbing quickly never shows a blank or
        broken frame.
  ------------------------------------------------------------------- */
  const loaderEl = document.getElementById("loader");
  const loaderFill = document.getElementById("loaderFill");
  const loaderPct = document.getElementById("loaderPct");

  const totalFrames = Object.values(SEQUENCES).reduce((a, s) => a + s.count, 0);
  let loadedFrames = 0;
  const imageCache = {}; // seqName -> array of Image objects (1-indexed via offset)

  function preloadAll() {
    return new Promise((resolve) => {
      Object.entries(SEQUENCES).forEach(([name, seq]) => {
        imageCache[name] = new Array(seq.count + 1);
        for (let i = 1; i <= seq.count; i++) {
          const img = new Image();
          img.src = framePath(name, i);
          img.onload = img.onerror = () => {
            loadedFrames++;
            const pct = Math.round((loadedFrames / totalFrames) * 100);
            if (loaderFill) loaderFill.style.width = pct + "%";
            if (loaderPct) loaderPct.textContent = pct + "%";
            if (loadedFrames >= totalFrames) resolve();
          };
          imageCache[name][i] = img;
        }
      });
    });
  }

  function frameSrc(seq, index) {
    const clamped = Math.min(Math.max(index, 1), SEQUENCES[seq].count);
    const img = imageCache[seq] && imageCache[seq][clamped];
    return img ? img.src : framePath(seq, clamped);
  }

  /* ----------------------------------------------------------------
     3. LAYOUT — map the four-beat narrative (System > Subsystem >
        Component > Latent Defect) onto the three supplied sequences:
        rocket (system), satellite (subsystem), and the lander/board
        sequence, whose own back half already dismantles down to a
        red-highlighted fault — carrying both the "component" and
        "defect" beats as one continuous, unbroken animation.
  ------------------------------------------------------------------- */
  const PXF_SOLO = 9;   // scroll px per frame while a product is the sole focus
  const PXF_TRANS = 15; // scroll px per frame while two products cross-fade

  const R = SEQUENCES.rocket.count;
  const S = SEQUENCES.satellite.count;
  const C = SEQUENCES.component.count;

  const rocketSoloEnd = 188;                 // rocket frames 1..188 solo
  const rocketTransCount = R - rocketSoloEnd; // remaining rocket frames used in transition 1

  const satTransInCount = rocketTransCount;   // satellite frames 1..N paired with rocket's tail
  const satSoloEnd = 196;                     // satellite frames N..196 solo
  const satTransOutCount = S - satSoloEnd;    // satellite tail used in transition 2

  const compTransInCount = satTransOutCount;  // component frames 1..N paired with satellite's tail
  // component frames N..241 solo — carries component reveal AND the defect close-up

  const len1 = (rocketSoloEnd - 1) * PXF_SOLO;
  const lenT1 = rocketTransCount * PXF_TRANS;
  const len2 = (satSoloEnd - satTransInCount) * PXF_SOLO;
  const lenT2 = satTransOutCount * PXF_TRANS;
  const len3 = (C - compTransInCount) * PXF_SOLO;

  const T1_START = len1;
  const T1_END = T1_START + lenT1;
  const SEG2_END = T1_END + len2;
  const T2_END = SEG2_END + lenT2;
  const TOTAL = T2_END + len3;

  // Point at which the component sequence visually reaches its
  // red-highlighted fault (roughly its last quarter) — used to tint
  // the progress indicator and as the "Defects" nav anchor.
  const DEFECT_FRAME_START = Math.round(C * 0.78);
  const defectLocal = TOTAL - ((C - DEFECT_FRAME_START) * PXF_SOLO);

  const pinWrapper = document.getElementById("pinWrapper");
  pinWrapper.style.height = `${TOTAL + window.innerHeight}px`;

  /* ----------------------------------------------------------------
     4. LAYER ELEMENTS
  ------------------------------------------------------------------- */
  function layerRefs(name) {
    const root = document.querySelector(`.layer[data-layer="${name}"]`);
    return {
      root,
      sharp: root.querySelector(".layer-sharp"),
      backdrop: root.querySelector(".layer-backdrop"),
    };
  }

  const layers = {
    rocket: layerRefs("rocket"),
    satellite: layerRefs("satellite"),
    component: layerRefs("component"),
  };

  const bgs = {
    rocket: document.querySelector(".stage-bg.bg-rocket"),
    satellite: document.querySelector(".stage-bg.bg-satellite"),
    component: document.querySelector(".stage-bg.bg-component"),
  };

  const progressFill = document.getElementById("progressFill");
  const progressRail = document.getElementById("progressRail");

  function applyLayer(name, frameIndex, opacity, scale, tx, ty, brightness) {
    const L = layers[name];
    if (opacity > 0.002) {
      const src = frameSrc(name, frameIndex);
      if (L.sharp.src !== location.origin + "/" + src && !L.sharp.src.endsWith(src)) {
        L.sharp.src = src;
        L.backdrop.src = src;
      }
    }
    L.root.style.opacity = opacity;
    if (reduceMotion) {
      L.root.style.transform = "none";
      L.root.style.filter = "none";
    } else {
      L.root.style.transform = `translate(${tx}%, ${ty}%) scale(${scale})`;
      L.root.style.filter = `brightness(${brightness})`;
    }
    bgs[name].style.opacity = opacity;
  }

  function hideLayer(name) {
    layers[name].root.style.opacity = 0;
    bgs[name].style.opacity = 0;
  }

  /* ----------------------------------------------------------------
     5. SCROLL -> FRAME MAPPING
  ------------------------------------------------------------------- */
  function render() {
    const wrapperTop = pinWrapper.offsetTop;
    const raw = window.scrollY - wrapperTop;
    const local = Math.min(Math.max(raw, 0), TOTAL);

    if (local <= T1_START) {
      // --- Rocket solo ---
      const frame = 1 + Math.round((local / Math.max(len1, 1)) * (rocketSoloEnd - 1));
      applyLayer("rocket", frame, 1, 1, 0, 0, 1);
      hideLayer("satellite");
      hideLayer("component");

    } else if (local <= T1_END) {
      // --- Transition: rocket -> satellite ---
      const p = (local - T1_START) / Math.max(lenT1, 1);
      const rocketFrame = rocketSoloEnd + Math.round(p * rocketTransCount);
      const satFrame = 1 + Math.round(p * satTransInCount);

      applyLayer("rocket", rocketFrame, 1 - p, 1 - p * 0.18, -p * 7, p * 5, 1 - p * 0.4);
      applyLayer("satellite", satFrame, p, 0.7 + p * 0.3, (1 - p) * 8, -(1 - p) * 6, 0.6 + p * 0.4);
      hideLayer("component");

    } else if (local <= SEG2_END) {
      // --- Satellite solo ---
      const p = (local - T1_END) / Math.max(len2, 1);
      const frame = satTransInCount + Math.round(p * (satSoloEnd - satTransInCount));
      applyLayer("satellite", frame, 1, 1, 0, 0, 1);
      hideLayer("rocket");
      hideLayer("component");

    } else if (local <= T2_END) {
      // --- Transition: satellite -> component ---
      const p = (local - SEG2_END) / Math.max(lenT2, 1);
      const satFrame = satSoloEnd + Math.round(p * satTransOutCount);
      const compFrame = 1 + Math.round(p * compTransInCount);

      applyLayer("satellite", satFrame, 1 - p, 1 - p * 0.18, -p * 7, p * 5, 1 - p * 0.4);
      applyLayer("component", compFrame, p, 0.7 + p * 0.3, (1 - p) * 8, -(1 - p) * 6, 0.6 + p * 0.4);
      hideLayer("rocket");

    } else {
      // --- Component solo (component reveal + latent-defect close) ---
      const p = (local - T2_END) / Math.max(len3, 1);
      const frame = compTransInCount + Math.round(p * (C - compTransInCount));
      applyLayer("component", frame, 1, 1, 0, 0, 1);
      hideLayer("rocket");
      hideLayer("satellite");
    }

    // Progress rail
    const overall = local / TOTAL;
    const railH = progressRail.clientHeight;
    progressFill.style.top = `${overall * (railH - 4)}px`;
    progressFill.classList.toggle("is-defect", local >= defectLocal);

    // Nav translucency
    nav.classList.toggle("scrolled", window.scrollY > 40);
  }

  let ticking = false;
  function onScroll() {
    if (!ticking) {
      requestAnimationFrame(() => {
        render();
        ticking = false;
      });
      ticking = true;
    }
  }

  /* ----------------------------------------------------------------
     6. NAV
  ------------------------------------------------------------------- */
  const nav = document.getElementById("nav");

  function scrollToLocal(target) {
    const wrapperTop = pinWrapper.offsetTop;
    window.scrollTo({ top: wrapperTop + target, behavior: "smooth" });
  }

  document.querySelectorAll("[data-goto]").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.preventDefault();
      const key = el.getAttribute("data-goto");
      if (key === "top") {
        window.scrollTo({ top: 0, behavior: "smooth" });
        return;
      }
      if (key === "platform") {
        const section = document.getElementById("platform");
        if (section) section.scrollIntoView({ behavior: "smooth", block: "start" });
        return;
      }
      const targets = {
        inspection: len1 * 0.5,
        reliability: T1_END + len2 * 0.45,
        defects: defectLocal + (TOTAL - defectLocal) * 0.4,
      };
      scrollToLocal(targets[key] ?? 0);
    });
  });

  /* ----------------------------------------------------------------
     7. BOOT
  ------------------------------------------------------------------- */
  document.body.classList.add("locked");

  preloadAll().then(() => {
    document.body.classList.remove("locked");
    loaderEl.classList.add("hidden");
    render();
  });

  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", () => {
    pinWrapper.style.height = `${TOTAL + window.innerHeight}px`;
    render();
  });

  // Initial paint (in case preload resolves before first scroll)
  render();
})();
