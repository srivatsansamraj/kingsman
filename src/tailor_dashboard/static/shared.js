// What the Jobs page (app.js), Profile (profile.js) and Stats (stats.js) share: the server's API, the job helpers,
// text and dates, and the animations.
export var REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
export var EASE = "cubic-bezier(.2,.8,.2,1)";

// Every change carries the X-Tailor header; the server refuses a change without it (another website's page could
// not add it).
export function api(path, options) {
  options = options || {};
  var init = { method: options.method || "GET", headers: {} };
  if (options.body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(options.body); }
  if (init.method !== "GET") init.headers["X-Tailor"] = "1";
  return fetch(path, init).then(function (response) {
    return response.json().catch(function () { return {}; }).then(function (data) {
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The server answered " + response.status);
      return data;
    });
  });
}

// ---------- data helpers ----------
// Each requirement's state on the job's page ({state, caps}); [] until the page is built.
export function reqsOf(job) { return job.reqs || []; }
// The page's coverage: the share of the posting's requirement weight it meets (a required item counts three times).
export function matchOf(job) { return job.match || 0; }
export function hasMatch(job) { return job.read && typeof job.match === "number" && !job.refused; }
export function tone(value) { return value >= .7 ? "tone-good" : value >= .5 ? "tone-mid" : "tone-low"; }
export function esc(text) { return String(text).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
export function dateText(iso) {
  var months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var parts = iso.split("-");
  return months[+parts[1] - 1] + " " + (+parts[2]);
}
export function isoDate(date) {
  return date.getFullYear() + "-" + String(date.getMonth() + 1).padStart(2, "0") + "-" + String(date.getDate()).padStart(2, "0");
}
export function todayIso() { return isoDate(new Date()); }
// Skill names are stored lower-case, as the engine keeps them; as a row heading the first letter is raised.
function heading(name) { return name.charAt(0).toUpperCase() + name.slice(1); }
// A count and its word, plural unless the count is one: "1 job", "3 jobs".
export function countText(count, word) { return count + " " + word + (count === 1 ? "" : "s"); }
// A ranked row: the name, a bar scaled to the largest count, and how many jobs.
export function gapRowHtml(name, count, most, toneName) {
  return '<div class="gap-row"><span class="item-name">' + esc(heading(name)) + '</span><span class="bar ' + toneName + '"><i data-w="' + (count / most * 100).toFixed(1) + '"></i></span><span class="mono">' + countText(count, "job") + "</span></div>";
}

// ---------- animations ----------
// Two frames: the browser draws what was just put in place, so a change made in the second one animates from it.
export function afterPaint(callback) { requestAnimationFrame(function () { requestAnimationFrame(callback); }); }

// The bars (data-w, a percentage of the width) and the chart's columns (data-h, a share of the height) grow to size.
export function fillBars(scope) {
  scope.querySelectorAll(".bar > i[data-w], .chart-col i[data-h]").forEach(function (bar) {
    var target = bar.hasAttribute("data-w") ? "scaleX(" + (+bar.getAttribute("data-w") / 100) + ")" : "scaleY(" + bar.getAttribute("data-h") + ")";
    if (REDUCED) { bar.style.transform = target; return; }
    afterPaint(function () { bar.style.transform = target; });
  });
}

export function countUp(scope) {
  scope.querySelectorAll("[data-count]").forEach(function (el) {
    var to = +el.getAttribute("data-count");
    if (REDUCED) { el.textContent = to; return; }
    var start = performance.now();
    function frame(now) {
      var t = Math.min(1, (now - start) / 700);
      el.textContent = Math.round(to * (1 - Math.pow(1 - t, 3)));
      if (t < 1) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  });
}

export function placePill(pill, width, left) {
  pill.style.width = width + "px";
  pill.style.transform = "translateX(" + left + "px)";
}

// Each element that moved since its top was measured (`tops`, in the same order) slides from there to its new place
// (transform only).
export function slideFrom(elements, tops, duration) {
  elements.forEach(function (el, index) {
    var dy = tops[index] - el.getBoundingClientRect().top;
    if (Math.abs(dy) > 1) el.animate([{ transform: "translateY(" + dy + "px)" }, { transform: "none" }], { duration: duration, easing: EASE });
  });
}
