// ---------- stats (`/api/stats`) ----------
// Each call's time, tokens, attempts and the model that answered, from the saved reading (call 1), mapping (call 2)
// and choice (call 3). Tokens are those of the calls a Claude model answered, which count toward the plan; Jev's
// requests, input tokens and price are counted apart.
import { api, reqsOf, matchOf, hasMatch, esc, dateText, isoDate, todayIso, countText, gapRowHtml, fillBars } from "./shared.js";

// What Stats takes from the Jobs page, set by `connectStats`: the jobs as the list has them now (`jobs()`), one by its
// id (`findJob`), and going to a job (`openJob`) or to Profile's coverage (`showCoverage`).
var jobsPage = null;
export function connectStats(connection) { jobsPage = connection; }

var statsData = { readings: {}, today: todayIso() };
export function loadStats() {
  return api("/api/stats").then(function (data) { statsData = data; return data; });
}
function isReadForStats(job) { return Boolean(statsData.readings[job.id]); }
function readingStats(job) { return statsData.readings[job.id]; }
// Call 3's cost to a posting; zeros where none was made (a legacy page, or a match that was not low).
var NO_CALL = { seconds: 0, input: 0, output: 0, unknown: false };
function call3Of(s) { return s.call3 || NO_CALL; }
// The calls a Claude model answered: their tokens count toward the plan, and Jev's do not.
function planCalls(s) { return [s.call1, s.call2, call3Of(s)].filter(function (call) { return !call.jev; }); }
function total(list, pick) { return list.reduce(function (sum, item) { return sum + pick(item); }, 0); }
function fmtSeconds(s) {
  if (s < 90) return s.toFixed(1) + " s";
  var whole = Math.round(s);
  return Math.floor(whole / 60) + " min " + (whole % 60) + " s";
}
function fmtTokens(n) { return n >= 1000 ? (n / 1000).toFixed(n >= 100000 ? 0 : 1) + "k" : String(n); }
function reachedStage(job, stage) {
  if (stage === "added") return true;
  if (stage === "read") return job.read;
  if (stage === "applied") return ["applied", "interview", "offer", "rejected"].indexOf(job.status) >= 0;
  if (stage === "interview") return ["interview", "offer"].indexOf(job.status) >= 0;
  return job.status === "offer";
}
function reachedCount(stage) { return jobsPage.jobs().filter(function (job) { return reachedStage(job, stage); }).length; }
var sortBy = { key: "read", dir: -1 }; // the table's order: a column's key, and 1 for rising or -1 for falling
var statsBody = document.getElementById("stats-body");
function statsRows() {
  var rows = jobsPage.jobs().filter(isReadForStats).map(function (job) {
    var s = readingStats(job);
    var reqs = reqsOf(job);
    var count = function (name) { return reqs.filter(function (r) { return r.state === name; }).length; };
    return { job: job, s: s, match: matchOf(job), met: count("met"), partial: count("partial"), missing: count("missing") };
  });
  var key = sortBy.key, dir = sortBy.dir;
  var value = {
    job: function (r) { return r.job.title.toLowerCase(); }, read: function (r) { return r.s.read; },
    call1: function (r) { return r.s.call1.seconds; }, call2: function (r) { return r.s.call2.seconds; }, call3: function (r) { return call3Of(r.s).seconds; },
    tokens: function (r) { return r.s.tokens; }, retries: function (r) { return r.s.retries; }, match: function (r) { return r.match; }
  }[key];
  rows.sort(function (a, b) { var x = value(a), y = value(b); return (x < y ? -1 : x > y ? 1 : 0) * dir; });
  return rows;
}
function chartHtml(values, labels, tones, titles) {
  var most = Math.max.apply(null, values.concat([1]));
  return '<div class="chart" style="--cols:' + values.length + '">' + values.map(function (v, i) {
    var h = v ? Math.max(v / most, .04) : .02;
    return '<div class="chart-col' + (v ? "" : " zero") + (tones ? " " + tones[i] : "") + '" title="' + esc(titles[i]) + '"><i data-h="' + h.toFixed(3) + '"></i></div>';
  }).join("") + '</div><div class="chart-axis" style="--cols:' + values.length + '">' + labels.map(function (l) { return "<span>" + l + "</span>"; }).join("") + "</div>";
}
export function renderStats() {
  var jobs = jobsPage.jobs();
  var read = jobs.filter(isReadForStats);
  var stats = read.map(readingStats);
  document.getElementById("stats-sub").textContent = "From the " + jobs.length + " jobs you track · tokens are Claude's, which count toward your plan's limits; Jev's are counted apart";
  statsBody.innerHTML = statCardsHtml(read, stats) +
    '<div class="stat-grid">' + funnelHtml() + matchSpreadHtml(read) + skillsPanelHtml(read) + usageHtml(stats) + worthALookHtml(read) + "</div>" +
    editStatsHtml(statsData.edits) + statsTableHtml();
  fillBars(statsBody);
}
function statCardsHtml(read, stats) {
  var jobs = jobsPage.jobs();
  var n = Math.max(read.length, 1);
  // Totals count every cost that was saved; averages are over the readings that saved both calls' costs (older
  // readings kept none for call 1), so a reading with one known call does not pull an average down. Call 3 counts
  // wherever it was made.
  var timedStats = stats.filter(function (s) { return !s.unknown; });
  var timed = timedStats.length;
  var nt = Math.max(timed, 1);
  var average = function (pick) { return Math.round(total(timedStats, pick) / nt); };
  var untimed = read.length - timed ? " · " + (read.length - timed) + " older readings kept no times" : "";
  var modelSeconds = total(stats, function (s) { return s.seconds; });
  var tokensIn = total(stats, function (s) { return total(planCalls(s), function (call) { return call.input; }); });
  var tokensOut = total(stats, function (s) { return total(planCalls(s), function (call) { return call.output; }); });
  // Jev's requests, off the plan: TypeSafe charges for input tokens only.
  var jevRequests = total(stats, function (s) { return s.jev.requests; });
  var jevDollars = total(stats, function (s) { return s.jev.dollars; });
  var jevLine = jevRequests ? '<div class="stat-sub">Jev: ' + countText(jevRequests, "request") + " · " + fmtTokens(total(stats, function (s) { return s.jev.input; })) +
    " input tokens · $" + jevDollars.toFixed(jevDollars < 0.01 ? 4 : 2) + "</div>" : "";
  var call3Term = stats.some(function (s) { return s.call3; }) ? " + " + average(function (s) { return call3Of(s).seconds; }) + " s" : "";
  return '<div class="stat-cards">' +
    '<div class="stat-card"><div class="stat-label">Jobs</div><div class="stat-value">' + jobs.length + '</div><div class="stat-sub">' + read.length + " read · " + (jobs.length - read.length) + " waiting</div></div>" +
    '<div class="stat-card"><div class="stat-label">Model time</div><div class="stat-value">' + fmtSeconds(modelSeconds) + '</div><div class="stat-sub">about ' + average(function (s) { return s.seconds; }) + " s a posting: " +
      average(function (s) { return s.call1.seconds; }) + " s + " + average(function (s) { return s.call2.seconds; }) + " s" + call3Term + ", then " + (total(stats, function (s) { return s.build; }) / n).toFixed(1) + " s to build" + untimed + "</div></div>" +
    '<div class="stat-card"><div class="stat-label">Tokens</div><div class="stat-value">' + fmtTokens(tokensIn + tokensOut) + '</div><div class="stat-sub">' + fmtTokens(tokensIn) + " in · " + fmtTokens(tokensOut) + " out · about " + fmtTokens(average(function (s) { return s.tokens; })) + " a posting</div>" + jevLine + "</div>" +
    '<div class="stat-card"><div class="stat-label">Applications</div><div class="stat-value">' + reachedCount("applied") + '</div><div class="stat-sub">' + reachedCount("interview") + " interviews · " + countText(reachedCount("offer"), "offer") + "</div></div>" +
    "</div>";
}
function funnelHtml() {
  var tracked = Math.max(jobsPage.jobs().length, 1);
  var stages = [["Added", "added"], ["Read", "read"], ["Applied", "applied"], ["Interview", "interview"], ["Offer", "offer"]];
  return '<section class="panel"><h3>Applications</h3>' + stages.map(function (stage, i) {
    var c = reachedCount(stage[1]);
    var prev = i ? reachedCount(stages[i - 1][1]) : 0;
    var rate = i ? (prev ? Math.round(c / prev * 100) + "% of " + stages[i - 1][0].toLowerCase() : "") : "";
    return '<div class="funnel-row"><span>' + stage[0] + '</span><span class="bar"><i data-w="' + (c / tracked * 100).toFixed(1) + '"></i></span><span class="mono">' + c + '</span><span class="rate">' + rate + "</span></div>";
  }).join("") + "</section>";
}
function matchSpreadHtml(read) {
  var bins = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
  var matchSum = 0;
  // A posting the engine built no page for has no match; it is left out here.
  var scored = read.filter(hasMatch);
  scored.forEach(function (job) { var m = matchOf(job); matchSum += m; bins[Math.min(9, Math.floor(m * 10))] += 1; });
  var noPage = read.length - scored.length ? ", " + (read.length - scored.length) + " with no page" : "";
  return '<section class="panel"><h3>Match scores <span class="aside">average ' + Math.round(matchSum / Math.max(scored.length, 1) * 100) + "%" + noPage + "</span></h3>" +
    chartHtml(bins, bins.map(function (_v, i) { return i * 10; }), bins.map(function (_v, i) { return i < 5 ? "tone-low" : i < 7 ? "tone-mid" : "tone-good"; }),
      bins.map(function (v, i) { return countText(v, "job") + " at " + i * 10 + " to " + (i * 10 + 9) + "%"; })) + "</section>";
}
function skillsPanelHtml(read) {
  // Each alternative capability a requirement names counts; a requirement with none (a language, tool or degree, not
  // a skill) counts toward no skill.
  var matched = {}, missing = {};
  read.forEach(function (job) {
    reqsOf(job).forEach(function (req) {
      req.caps.forEach(function (cap) {
        if (req.state === "met") matched[cap] = (matched[cap] || 0) + 1;
        if (req.state === "missing") missing[cap] = (missing[cap] || 0) + 1;
      });
    });
  });
  function topList(table, toneName) {
    var entries = Object.keys(table).map(function (k) { return [k, table[k]]; }).sort(function (a, b) { return b[1] - a[1]; }).slice(0, 4);
    var most = entries.length ? entries[0][1] : 1;
    return entries.map(function (e) { return gapRowHtml(e[0], e[1], most, toneName); }).join("");
  }
  return '<section class="panel"><h3>Skills across your jobs <button type="button" class="list-link" data-coverage>See coverage</button></h3><div class="split-lists">' +
    '<div><div class="mini-head">Most often met</div>' + topList(matched, "tone-good") + "</div>" +
    '<div><div class="mini-head">Most often missing</div>' + topList(missing, "tone-low") + "</div></div></section>";
}
function usageHtml(stats) {
  var days = [], perDay = {};
  stats.forEach(function (s) { perDay[s.read] = (perDay[s.read] || 0) + s.tokens; });
  // The ten days up to today.
  var last = new Date(statsData.today + "T12:00:00");
  for (var back = 9; back >= 0; back -= 1) {
    var day = new Date(last.getTime() - back * 864e5);
    days.push(isoDate(day));
  }
  var dayTokens = days.map(function (day) { return perDay[day] || 0; });
  var most = Math.max.apply(null, dayTokens);
  var busiest = most ? "busiest " + dateText(days[dayTokens.indexOf(most)]) + ", " + fmtTokens(most) + " tokens" : "no Claude use in these ten days";
  return '<section class="panel"><h3>Claude use by day <span class="aside">' + busiest + "</span></h3>" +
    chartHtml(dayTokens, days.map(function (day) { return String(+day.slice(8)); }), null,
      days.map(function (day, i) { return dateText(day) + ": " + fmtTokens(dayTokens[i]) + " tokens"; })) + "</section>";
}
function worthALookHtml(read) {
  var slowest = read.slice().sort(function (a, b) { return readingStats(b).seconds - readingStats(a).seconds; }).slice(0, 3);
  var retried = read.filter(function (job) { return readingStats(job).retries > 0; });
  var lookIds = [];
  slowest.concat(retried).forEach(function (job) { if (lookIds.indexOf(job.id) < 0) lookIds.push(job.id); });
  var callTime = function (call) { return call.unknown ? "time not kept" : fmtSeconds(call.seconds); };
  return '<section class="panel"><h3>Worth a look</h3>' + lookIds.map(function (id) {
    var job = jobsPage.findJob(id), s = readingStats(job);
    return '<button type="button" class="look-row" data-job="' + esc(id) + '"><span class="item-name">' + esc(job.title) + "</span>" +
      (s.retries ? '<span class="retry-tag">asked again</span>' : '<span class="mono muted">' + fmtSeconds(s.seconds) + "</span>") +
      "<small>" + esc(job.company) + " · requirements " + callTime(s.call1) + ", capabilities " + callTime(s.call2) + (s.call3 ? ", projects " + callTime(s.call3) : "") +
      (s.retries ? " (asked again: an answer could not be read, or Jev was busy, failed or could not be reached)" : "") + "</small></button>";
  }).join("") + "</section>";
}
function statsTableHtml() {
  var columns = [["job", "Job", ""], ["read", "Read", ""], ["call1", "Requirements", "num"], ["call2", "Capabilities", "num"], ["call3", "Projects", "num"], ["tokens", "Tokens", "num"], ["retries", "Retries", "num"], ["match", "Match", "num"]];
  // Under the time, the model that answered; where it answered in place of the one asked, the reason is its title.
  var byHtml = function (call) {
    if (!call.by) return "";
    return "<small" + (call.fallback ? ' title="' + esc(call.fallback) + '"' : "") + ">" + esc(call.by) + (call.fallback ? " · fallback" : "") + "</small>";
  };
  var callCell = function (call) { return '<td class="num">' + (!call || call.unknown ? "–" : fmtSeconds(call.seconds) + byHtml(call)) + "</td>"; };
  return '<section class="panel"><h3>Every read job</h3><div class="table-wrap"><table class="stats-table"><thead><tr>' +
    columns.map(function (c) {
      var sorted = sortBy.key === c[0];
      return '<th class="' + c[2] + '"' + (sorted ? ' aria-sort="' + (sortBy.dir > 0 ? "ascending" : "descending") + '"' : "") + '><button type="button" data-sort-by="' + c[0] + '">' + c[1] +
        (sorted ? '<span class="arrow" aria-hidden="true">↓</span>' : "") + "</button></th>";
    }).join("") + "</tr></thead><tbody>" + statsRows().map(function (r) {
      return '<tr data-job="' + esc(r.job.id) + '" tabindex="0"><td class="job-cell">' + esc(r.job.title) + "<small>" + esc(r.job.company) + "</small></td>" +
        '<td class="mono">' + dateText(r.s.read) + "</td>" + callCell(r.s.call1) + callCell(r.s.call2) + callCell(r.s.call3) +
        '<td class="num">' + (r.s.unknown ? "–" : fmtTokens(r.s.tokens)) + '</td><td class="num">' + (r.s.retries || "–") + '</td><td class="num">' + (hasMatch(r.job) ? Math.round(r.match * 100) + "% · " + r.met + "/" + r.partial + "/" + r.missing : "No page") + "</td></tr>";
    }).join("") + "</tbody></table></div></section>";
}

// The pages as built against the ones the user edited from the judge's proposals.
function editStatsHtml(edits) {
  if (!edits) return "";
  var counts = edits.decisions || {};
  var rows = (edits.pages || []).map(function (entry) {
    var job = jobsPage.findJob(entry.id) || { title: entry.id, company: "" };
    var delta = Math.round((entry.edited.coverage - entry.engine.coverage) * 100);
    return '<button type="button" class="versus-row" data-job="' + esc(entry.id) + '"><span class="item-name">' + esc(job.title) + "<small>" + esc(job.company) + "</small></span>" +
      '<span class="mono">' + Math.round(entry.engine.coverage * 100) + "% → " + Math.round(entry.edited.coverage * 100) + "%</span>" +
      '<span class="mono ' + (delta > 0 ? "up" : delta < 0 ? "down" : "") + '">' + (delta > 0 ? "+" : "") + delta + "</span>" +
      '<span class="mono">' + entry.engine.required_met + " → " + entry.edited.required_met + " of " + entry.edited.required + " required</span>" +
      '<span class="mono muted">' + entry.changed + " bullets</span></button>";
  }).join("");
  var decided = (counts.accepted || 0) + (counts.dismissed || 0);
  return '<section class="panel versus"><h3>Built page vs judge-assisted <span class="aside">' + (counts.accepted || 0) + " accepted · " + (counts.dismissed || 0) + " dismissed · " + (counts.undone || 0) + " undone</span></h3>" +
    (rows || '<p class="section-note" style="margin:0">No page edited yet. Ask the judge on a job; its suggested changes come with Yes and No.</p>') +
    (decided ? '<p class="section-note versus-note">The engine\'s own score ranked the page you chose lower for ' + edits.engine_scored_lower + " of " + edits.accepted_scored + " accepted changes. If that share stays high over many, the selector's weights disagree with you; if the added bullets simply lack a capability tag, the library does.</p>" : "") +
    "</section>";
}

statsBody.addEventListener("click", function (event) {
  var sortButton = event.target.closest("[data-sort-by]");
  if (sortButton) {
    var key = sortButton.getAttribute("data-sort-by");
    sortBy = sortBy.key === key ? { key: key, dir: -sortBy.dir } : { key: key, dir: key === "job" ? 1 : -1 };
    var scroll = statsBody.scrollTop;
    renderStats();
    statsBody.scrollTop = scroll;
    return;
  }
  if (event.target.closest("[data-coverage]")) { jobsPage.showCoverage(); return; }
  var row = event.target.closest("[data-job]");
  if (row) jobsPage.openJob(row.getAttribute("data-job"));
});
statsBody.addEventListener("keydown", function (event) {
  if (event.key !== "Enter" && event.key !== " ") return;
  var row = event.target.closest("tr[data-job]");
  if (!row) return;
  event.preventDefault();
  jobsPage.openJob(row.getAttribute("data-job"));
});
