// HayClips clip editor: live preview in the browser (no server work while you drag or tweak).
// The vertical preview is the landscape window cropped with CSS exactly like the render crop; captions
// and the hook are HTML overlays placed with the same normalised (x, y) the render uses.
(function () {
  "use strict";

  // ---------- shared: platform safe-zone overlays (editor and export) ----------
  function drawZones(layer, zones, platform) {
    layer.innerHTML = "";
    if (!platform || !zones[platform]) return [];
    zones[platform].zones.forEach(function (z) {
      var d = document.createElement("div");
      d.className = "zone";
      d.style.left = (z.box[0] * 100) + "%"; d.style.top = (z.box[1] * 100) + "%";
      d.style.width = ((z.box[2] - z.box[0]) * 100) + "%"; d.style.height = ((z.box[3] - z.box[1]) * 100) + "%";
      var l = document.createElement("span"); l.className = "zone-label"; l.textContent = zones[platform].label + " · " + z.name;
      d.appendChild(l);
      layer.appendChild(d);
    });
    return zones[platform].zones;
  }
  function wirePlatforms(group, onChange) {
    group.querySelectorAll("[data-platform]").forEach(function (b) {
      b.addEventListener("click", function () {
        group.querySelectorAll("[data-platform]").forEach(function (x) { x.setAttribute("aria-pressed", String(x === b)); });
        onChange(b.getAttribute("data-platform"));
      });
    });
  }

  // export page: zones over the rendered video
  var exp = document.querySelector("[data-export-platforms]");
  if (exp) {
    var zonesE = JSON.parse(exp.getAttribute("data-zones-json"));
    var layerE = document.querySelector("[data-export-frame] [data-zones]");
    wirePlatforms(exp, function (p) { drawZones(layerE, zonesE, p); });
  }

  var root = document.querySelector("[data-editor]");
  var dataEl = document.getElementById("editor-data");
  if (!root || !dataEl) return;
  var D = JSON.parse(dataEl.textContent);
  var box = root.querySelector("[data-preview-box]");
  var video = root.querySelector("[data-video]");
  var cap = root.querySelector("[data-caption]");
  var hookEl = root.querySelector("[data-hook-box]");
  var layer = root.querySelector("[data-zones]");
  var warn = root.querySelector("[data-safe-warn]");
  var moveSafe = root.querySelector("[data-move-safe]");
  var look = Object.assign({}, D.look), hook = Object.assign({}, D.hook_look);
  var cut = { start: D.cut.start, end: D.cut.end };
  var platform = "";
  var GROUPS = { L: [24, 5], A: [22, 4], B: [14, 2], C: [30, 6] };
  var STYLE = { clean: "L", active: "A", punch: "B", karaoke: "C" };
  var BASE = { L: 56, A: 58, B: 76, C: 46 };
  var crop = D.crop;

  video.src = root.getAttribute("data-wide");
  video.muted = false;

  // ---------- crop: same per-shot x as the render (x of a crop_w window over the landscape frame) ----------
  function shotX(t) {
    if (!crop) return null;
    var x = crop.shots[0].x;
    crop.shots.forEach(function (s) { if (t >= s.start - 1e-6) x = s.x; });
    return x;
  }
  function layoutVideo() {
    var H = box.clientHeight, W = box.clientWidth;
    var vw = video.videoWidth || 16, vh = video.videoHeight || 9;
    var dispW = H * vw / vh;
    video.style.height = H + "px"; video.style.width = dispW + "px";
    var x = shotX(video.currentTime);
    var tx = (x === null || !crop) ? -(dispW - W) / 2 : -(x / crop.width) * dispW;
    video.style.transform = "translateX(" + tx + "px)";
  }

  // ---------- captions: client-side screens like the renderer (approximate preview) ----------
  function screens() {
    var st = STYLE[look.preset], g = GROUPS[st], maxChars = g[0], maxWords = look.words_per_line ? +look.words_per_line : g[1];
    var out = [], cur = [];
    var flush = function () { if (cur.length) { out.push(cur); cur = []; } };
    D.words.forEach(function (w) {
      var text = w[2];
      var width = cur.map(function (x) { return x[2]; }).concat([text]).join(" ").length;
      if (cur.length && (width > maxChars || cur.length >= maxWords || w[0] - cur[cur.length - 1][1] > 0.5)) flush();
      cur.push(w);
      if (/[.!?։:…]$/.test(text)) flush();
    });
    flush();
    return out;
  }
  var SCREENS = screens();
  function captionAt(t) {
    for (var i = 0; i < SCREENS.length; i++) {
      var s = SCREENS[i], next = SCREENS[i + 1];
      var end = next ? next[0][0] : s[s.length - 1][1] + 0.4;
      if (t >= s[0][0] && t < end) return s;
    }
    return null;
  }
  function esc(s) { return s.replace(/[&<>]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]; }); }
  function renderCaption(t) {
    var st = STYLE[look.preset];
    var scr = captionAt(t) || (SCREENS[0] || null);
    var H = box.clientHeight, scale = H / 1280;
    cap.className = "cap-overlay st-" + st + (look.background && st !== "C" ? " boxed" : "");
    cap.style.left = (look.x * 100) + "%"; cap.style.top = (look.y * 100) + "%";
    cap.style.fontSize = Math.round(BASE[st] * look.size * scale * (st === "B" ? 0.95 : 1)) + "px";
    cap.style.color = look.color; cap.style.setProperty("--hl", look.highlight);
    if (!scr) { cap.textContent = "Captions"; return; }
    cap.innerHTML = scr.map(function (w) {
      var on = st === "A" && t >= w[0] && t < w[1];
      return on ? '<span class="hl">' + esc(w[2]) + "</span>" : esc(w[2]);
    }).join(" ");
  }
  function renderHook(t) {
    var text = (root.querySelector("[data-hook-text]") || {}).value || "";
    var visible = hook.show && text.trim() && (t < hook.duration || video.paused);
    hookEl.style.display = visible ? "block" : "none";
    hookEl.textContent = text;
    hookEl.style.left = (hook.x * 100) + "%"; hookEl.style.top = (hook.y * 100) + "%";
    hookEl.style.fontSize = Math.round(44 * box.clientHeight / 1280) + "px";
  }

  // ---------- safe zones ----------
  function checkSafe() {
    var zones = platform ? D.zones[platform].zones : [];
    var b = box.getBoundingClientRect(), r = cap.getBoundingClientRect();
    var c = [(r.left - b.left) / b.width, (r.top - b.top) / b.height, (r.right - b.left) / b.width, (r.bottom - b.top) / b.height];
    var hits = zones.filter(function (z) { return c[0] < z.box[2] && c[2] > z.box[0] && c[1] < z.box[3] && c[3] > z.box[1]; });
    if (hits.length) {
      warn.classList.remove("ok");
      warn.textContent = "Captions overlap the " + D.zones[platform].label + " " + hits.map(function (z) { return z.name; }).join(" and ") + " area.";
      moveSafe.classList.remove("hidden");
    } else {
      warn.classList.add("ok");
      warn.textContent = platform ? "Captions are clear of " + D.zones[platform].label + " UI." : "";
      moveSafe.classList.add("hidden");
    }
  }
  wirePlatforms(root, function (p) { platform = p; drawZones(layer, D.zones, p); checkSafe(); });
  moveSafe.addEventListener("click", function () {
    if (!platform) return;
    look.y = Math.min(look.y, D.safe_bottom[platform]);
    look.x = 0.5;
    var b = box.getBoundingClientRect(), r = cap.getBoundingClientRect();
    var top = look.y - (r.height / b.height);
    D.zones[platform].zones.forEach(function (z) {          // keep clear of a top bar too
      if (z.box[1] === 0 && top < z.box[3]) look.y = z.box[3] + (r.height / b.height) + 0.01;
    });
    markDirty(); draw();
  });

  // ---------- drag captions / hook (pointer events; keyboard arrows as an alternative) ----------
  function draggable(el, target, after) {
    var dragging = false;
    el.addEventListener("pointerdown", function (e) { dragging = true; el.classList.add("dragging"); el.setPointerCapture(e.pointerId); e.preventDefault(); });
    el.addEventListener("pointermove", function (e) {
      if (!dragging) return;
      var b = box.getBoundingClientRect();
      target.x = Math.min(0.95, Math.max(0.05, (e.clientX - b.left) / b.width));
      target.y = Math.min(0.98, Math.max(0.05, (e.clientY - b.top) / b.height));
      after(); draw();
    });
    el.addEventListener("pointerup", function () { dragging = false; el.classList.remove("dragging"); });
    el.addEventListener("keydown", function (e) {
      var step = e.shiftKey ? 0.05 : 0.01, moved = true;
      if (e.key === "ArrowUp") target.y -= step; else if (e.key === "ArrowDown") target.y += step;
      else if (e.key === "ArrowLeft") target.x -= step; else if (e.key === "ArrowRight") target.x += step; else moved = false;
      if (moved) { e.preventDefault(); target.x = Math.min(0.95, Math.max(0.05, target.x)); target.y = Math.min(0.98, Math.max(0.05, target.y)); after(); draw(); }
    });
  }
  var lookForm = root.querySelector("[data-look-form]");
  var unsaved = root.querySelector("[data-unsaved]");
  function markDirty() {
    lookForm.querySelector('[data-look="x"]').value = look.x.toFixed(4);
    lookForm.querySelector('[data-look="y"]').value = look.y.toFixed(4);
    if (unsaved) unsaved.textContent = "Unsaved changes";
  }
  draggable(cap, look, markDirty);
  draggable(hookEl, hook, function () {
    root.querySelector("[data-hook-x]").value = hook.x.toFixed(4);
    root.querySelector("[data-hook-y]").value = hook.y.toFixed(4);
  });

  // style controls update the preview immediately
  lookForm.querySelectorAll('input[name="preset"]').forEach(function (r) {
    r.addEventListener("change", function () { look.preset = r.value; SCREENS = screens(); markDirty(); draw(); });
  });
  var sizeLabel = lookForm.querySelector("[data-size-label]");
  [["size", "input"], ["color", "input"], ["highlight", "input"], ["words_per_line", "input"], ["background", "change"]].forEach(function (p) {
    var el = lookForm.querySelector('[data-look="' + p[0] + '"]');
    if (!el) return;
    el.addEventListener(p[1], function () {
      look[p[0]] = el.type === "checkbox" ? el.checked : (p[0] === "size" ? parseFloat(el.value) : el.value);
      if (p[0] === "words_per_line") SCREENS = screens();
      markDirty(); draw();
    });
  });
  ["[data-hook-text]", "[data-hook-show]", "[data-hook-duration]"].forEach(function (sel) {
    var el = root.querySelector(sel);
    if (el) el.addEventListener("input", function () {
      if (sel === "[data-hook-show]") hook.show = el.checked;
      if (sel === "[data-hook-duration]") hook.duration = parseFloat(el.value) || 3;
      draw();
    });
  });
  var hs = root.querySelector("[data-hook-show]");
  if (hs) hs.addEventListener("change", function () { hook.show = hs.checked; draw(); });

  // ---------- modes ----------
  root.parentElement.querySelectorAll("[data-mode]").forEach(function (b) {
    b.addEventListener("click", function () {
      document.querySelectorAll("[data-mode]").forEach(function (x) { x.setAttribute("aria-selected", String(x === b)); });
      document.querySelectorAll("[data-mode-panel]").forEach(function (p) { p.hidden = p.getAttribute("data-mode-panel") !== b.getAttribute("data-mode"); });
    });
  });

  // ---------- trim (window-relative cut, previewed live) ----------
  var cs = root.querySelector('[data-cut="start"]'), ce = root.querySelector('[data-cut="end"]');
  var cutLabel = root.querySelector("[data-cut-label]"), trimField = root.querySelector("[data-trim-field]");
  function mmss(t) { t = Math.max(0, t); var m = Math.floor(t / 60), s = Math.floor(t % 60); return m + ":" + (s < 10 ? "0" : "") + s; }
  function syncCut(moved) {
    var a = parseFloat(cs.value), b = parseFloat(ce.value);
    if (b - a < 3) { if (moved === cs) { a = b - 3; cs.value = a; } else { b = a + 3; ce.value = b; } }
    cut.start = a; cut.end = b;
    cutLabel.textContent = "Clip length " + Math.round(b - a) + " s";
    trimField.value = a.toFixed(2) + ":" + b.toFixed(2);
    if (moved) video.currentTime = moved === ce ? Math.max(a, b - 1.5) : a;
  }
  if (cs && ce) {
    cs.addEventListener("input", function () { syncCut(cs); });
    ce.addEventListener("input", function () { syncCut(ce); });
    root.querySelectorAll("[data-cut-nudge]").forEach(function (b) {
      b.addEventListener("click", function () {
        var el = b.getAttribute("data-cut-nudge") === "start" ? cs : ce;
        el.value = (parseFloat(el.value) + parseFloat(b.getAttribute("data-by"))).toFixed(2);
        syncCut(el);
      });
    });
    cutLabel.textContent = "Clip length " + Math.round(cut.end - cut.start) + " s";
  }

  // ---------- framing sliders move the live crop ----------
  root.querySelectorAll("[data-shot]").forEach(function (sl) {
    sl.addEventListener("input", function () {
      var idx = +sl.getAttribute("data-shot");
      crop.shots.forEach(function (s) { if (s.index === idx) { s.x = parseInt(sl.value, 10); video.currentTime = Math.max(cut.start, s.start + 0.05); } });
      layoutVideo();
    });
  });

  // ---------- playback inside the cut ----------
  var pp = root.querySelector("[data-playpause]"), timeEl = root.querySelector("[data-time]");
  pp.addEventListener("click", function () {
    if (video.paused) { if (video.currentTime < cut.start || video.currentTime >= cut.end - 0.05) video.currentTime = cut.start; video.play(); }
    else video.pause();
  });
  video.addEventListener("play", function () { pp.textContent = "Pause"; });
  video.addEventListener("pause", function () { pp.textContent = "Play"; draw(); });
  video.addEventListener("loadedmetadata", function () { video.currentTime = cut.start; layoutVideo(); draw(); });
  video.addEventListener("timeupdate", function () { if (video.currentTime >= cut.end) { video.pause(); video.currentTime = cut.start; } });

  function draw() {
    var t = Math.max(0, video.currentTime - cut.start) + D.cut.start - cut.start;
    var rel = Math.max(0, video.currentTime - D.cut.start);
    renderCaption(rel);
    renderHook(Math.max(0, video.currentTime - cut.start));
    if (sizeLabel) sizeLabel.textContent = Math.round(look.size * 100) + "%";
    timeEl.textContent = mmss(Math.max(0, video.currentTime - cut.start)) + " / " + mmss(cut.end - cut.start);
    layoutVideo();
    checkSafe();
    void t;
  }
  (function loop() { if (!video.paused) draw(); requestAnimationFrame(loop); })();
  window.addEventListener("resize", draw);
  draw();
  window.HayClipsEditor = { look: look, hook: hook, cut: cut, checkSafe: checkSafe };
})();
