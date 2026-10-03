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

  window.HayClips = { csrf: csrf };
})();
