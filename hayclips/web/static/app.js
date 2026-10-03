// Polls the jobs panel (no inline scripts: CSP is script-src 'self').
(function () {
  var el = document.querySelector("[data-poll]");
  if (!el) return;
  var url = el.getAttribute("data-poll");
  function tick() {
    fetch(url, { credentials: "same-origin", headers: { "Accept": "text/html" } })
      .then(function (r) { return r.ok ? r.text() : null; })
      .then(function (html) { if (html !== null) el.innerHTML = html; })
      .catch(function () {})
      .finally(function () { setTimeout(tick, 2000); });
  }
  setTimeout(tick, 2000);
})();
