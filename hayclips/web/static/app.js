// HayClips UI behaviour. No inline scripts (CSP script-src 'self'); everything hooks onto data attributes.
(function () {
  "use strict";
  var csrf = (document.querySelector('#csrf-holder input[name="csrf"]') || {}).value || "";

  // jobs panel polling
  var poll = document.querySelector("[data-poll]");
  if (poll) {
    var url = poll.getAttribute("data-poll");
    var tick = function () {
      fetch(url, { credentials: "same-origin", headers: { "Accept": "text/html" } })
        .then(function (r) { return r.ok ? r.text() : null; })
        .then(function (html) { if (html !== null) poll.innerHTML = html; })
        .catch(function () {})
        .finally(function () { setTimeout(tick, 2000); });
    };
    setTimeout(tick, 2000);
  }

  // reload when the page says work is in progress (cheap: one GET every 3 s, only while needed)
  if (document.querySelector("[data-refresh-while-busy]")) {
    setTimeout(function () { window.location.reload(); }, 3000);
  }

  // tabs (new video page)
  document.querySelectorAll("[data-tabs]").forEach(function (tabs) {
    tabs.querySelectorAll("[data-tab]").forEach(function (b) {
      b.addEventListener("click", function () {
        tabs.querySelectorAll("[data-tab]").forEach(function (x) { x.setAttribute("aria-pressed", String(x === b)); });
        document.querySelectorAll("[data-panel]").forEach(function (p) {
          p.hidden = p.getAttribute("data-panel") !== b.getAttribute("data-tab");
        });
      });
    });
  });

  // upload: the file itself is the request body (streamed by the server, size-capped, validated)
  var up = document.getElementById("upload-form");
  if (up) {
    up.addEventListener("submit", function (e) {
      e.preventDefault();
      var file = up.querySelector('input[type="file"]').files[0];
      var name = up.querySelector('input[name="name"]').value.trim();
      var status = up.querySelector("[data-upload-status]");
      if (!file || !name) { status.textContent = "Choose a file and give it a name."; return; }
      var xhr = new XMLHttpRequest();
      xhr.open("POST", up.getAttribute("data-upload-url"));
      xhr.setRequestHeader("X-CSRF-Token", up.getAttribute("data-csrf") || csrf);
      xhr.setRequestHeader("X-Filename", encodeURIComponent(file.name));
      xhr.setRequestHeader("X-Project-Name", encodeURIComponent(name));
      xhr.setRequestHeader("Content-Type", "application/octet-stream");
      xhr.upload.onprogress = function (ev) {
        if (ev.lengthComputable) status.textContent = "Uploading… " + Math.round(100 * ev.loaded / ev.total) + "%";
      };
      xhr.onload = function () {
        var data = {};
        try { data = JSON.parse(xhr.responseText); } catch (err) { /* ignore */ }
        if (xhr.status === 200 && data.redirect) { window.location.href = data.redirect; }
        else { status.textContent = data.error || ("Upload failed (" + xhr.status + ")"); status.classList.add("bad"); }
      };
      xhr.onerror = function () { status.textContent = "Upload failed. Check the connection and try again."; };
      status.classList.remove("bad");
      status.textContent = "Uploading…";
      xhr.send(file);
    });
  }

  // ---- Brand Kit logo (raw upload, size-capped and type-checked by the server) ----
  var logo = document.getElementById("logo-form");
  if (logo) {
    logo.addEventListener("submit", function (e) {
      e.preventDefault();
      var f = logo.querySelector("[data-logo-file]").files[0], st = logo.querySelector("[data-logo-status]");
      if (!f) { st.textContent = "Choose an image first."; return; }
      fetch(logo.getAttribute("data-upload-url"), { method: "POST", body: f, credentials: "same-origin",
        headers: { "X-CSRF-Token": csrf, "Content-Type": "application/octet-stream" } })
        .then(function (r) { return r.json().then(function (d) { return [r.ok, d]; }); })
        .then(function (x) { if (x[0]) window.location.reload(); else st.textContent = x[1].error || "Upload failed"; })
        .catch(function () { st.textContent = "Upload failed"; });
    });
  }

  // ---- Choose clips: live selection summary and pre-transcription trim (all in the browser; free) ----
  function fmt(sec) {
    sec = Math.max(0, Math.round(sec));
    var m = Math.floor(sec / 60), s = sec % 60;
    return m ? (m + ":" + (s < 10 ? "0" : "") + s) : (s + " s");
  }
  function mmss(sec) { var m = Math.floor(sec / 60), s = Math.floor(sec % 60); return m + ":" + (s < 10 ? "0" : "") + s; }
  var chooser = document.getElementById("choose-form");
  if (chooser) {
    var cards = chooser.querySelectorAll("[data-cand]");
    var summarise = function () {
      var n = 0, total = 0;
      cards.forEach(function (card) {
        var box = card.querySelector('input[name="choose"]');
        card.classList.toggle("on", box.checked);
        if (box.checked) { n += 1; total += parseFloat(card.dataset.end) - parseFloat(card.dataset.start); }
      });
      var el = chooser.querySelector("[data-summary]");
      if (el) el.textContent = n + " selected · " + fmt(total);
      var add = chooser.querySelector("[data-add-captions]");
      if (add) add.disabled = n === 0;
    };
    cards.forEach(function (card) {
      card.querySelector('input[name="choose"]').addEventListener("change", summarise);
      var media = card.querySelector("[data-preview]");
      if (!media) return;
      var video = media.querySelector("video");
      var ps = parseFloat(media.dataset.pstart), pe = parseFloat(media.dataset.pend);
      var trim = media.querySelector("[data-trim]");
      if (!trim) return;
      var hs = trim.querySelector('[data-handle="start"]'), he = trim.querySelector('[data-handle="end"]');
      var value = trim.querySelector("[data-trim-value]"), label = trim.querySelector("[data-trim-label]");
      [hs, he].forEach(function (h) { h.min = ps; h.max = pe; });
      hs.value = card.dataset.start; he.value = card.dataset.end;
      var sync = function (moved) {
        var a = parseFloat(hs.value), b = parseFloat(he.value);
        if (b - a < 5) { if (moved === hs) { a = b - 5; hs.value = a; } else { b = a + 5; he.value = b; } }
        card.dataset.start = a.toFixed(2); card.dataset.end = b.toFixed(2);
        var changed = Math.abs(a - parseFloat(card.dataset.suggestedStart)) > 0.04 || Math.abs(b - parseFloat(card.dataset.suggestedEnd)) > 0.04;
        value.value = a.toFixed(2) + ":" + b.toFixed(2);
        label.textContent = mmss(a) + "–" + mmss(b) + " · " + Math.round(b - a) + " s" + (changed ? " (trimmed)" : "");
        var range = card.querySelector("[data-range]");
        if (range) range.innerHTML = mmss(a) + "–" + mmss(b) + " · <span data-len>" + Math.round(b - a) + "</span> s";
        if (moved && video.readyState > 0) video.currentTime = Math.max(0, (moved === he ? b - 1 : a) - ps);
        summarise();
      };
      hs.addEventListener("input", function () { sync(hs); });
      he.addEventListener("input", function () { sync(he); });
      trim.querySelectorAll("[data-nudge]").forEach(function (b) {
        b.addEventListener("click", function () {
          var h = b.dataset.nudge === "start" ? hs : he;
          h.value = (parseFloat(h.value) + parseFloat(b.dataset.by)).toFixed(2);
          sync(h);
        });
      });
      trim.querySelector("[data-reset]").addEventListener("click", function () {
        hs.value = card.dataset.suggestedStart; he.value = card.dataset.suggestedEnd; sync(hs);
      });
      var stopAt = null;
      trim.querySelector("[data-play]").addEventListener("click", function () {
        if (!video.paused) { video.pause(); return; }
        video.currentTime = Math.max(0, parseFloat(hs.value) - ps);
        stopAt = parseFloat(he.value) - ps;
        video.play();
      });
      video.addEventListener("timeupdate", function () { if (stopAt !== null && video.currentTime >= stopAt) { video.pause(); stopAt = null; } });
      video.controls = true;
      sync(null);
      value.value = "";   // nothing changes on the server unless the user moves a handle
      hs.addEventListener("change", function () { value.value = parseFloat(hs.value).toFixed(2) + ":" + parseFloat(he.value).toFixed(2); });
      he.addEventListener("change", function () { value.value = parseFloat(hs.value).toFixed(2) + ":" + parseFloat(he.value).toFixed(2); });
      trim.querySelectorAll("[data-nudge], [data-reset]").forEach(function (b) {
        b.addEventListener("click", function () { value.value = parseFloat(hs.value).toFixed(2) + ":" + parseFloat(he.value).toFixed(2); });
      });
    });
    summarise();
  }

  window.HayClips = { csrf: csrf, fmt: fmt };
})();
