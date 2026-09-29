// The Jobs page: the list, the open job, reading and the judge. Profile and Stats are their own modules.
import {
  REDUCED, EASE, api, reqsOf, matchOf, hasMatch, tone, esc, dateText, todayIso, afterPaint, fillBars, countUp, placePill, slideFrom
} from "./shared.js";
import { loadProfile, showProfile, showSection } from "./profile.js";
import { connectStats, loadStats, renderStats } from "./stats.js";

var STATUSES = ["added", "read", "applied", "interview", "rejected", "offer"];
var STATUS_LABEL = { added: "Added", read: "Read", applied: "Applied", interview: "Interview", rejected: "Rejected", offer: "Offer" };

// Jobs as the server lists them (`/api/jobs`); a job's full record (`/api/jobs/<id>`) is kept in `details`.
var jobs = [];
var details = {};
// Median seconds of each model call over the most recent saved records, for the cost shown before anything is spent;
// replaced by the list's `seconds` before anything is drawn.
var callSeconds = { requirements: 30, mapping: 30, projects: 0.5 };

var state = {
  openId: null, // the job open beside the list
  text: "", // the search
  sponsor: false, // the two filters
  remote: false,
  statusFilter: null, // the status chip pressed, or null for all
  sort: "match", // "match" or "date"
  firstRender: true, // the first list drawn: its rows rise in
  reading: {}, // job id -> its reading step now (see "reading" below)
  readingAgain: {}, // job id -> true while Read again runs, which makes every call
  sawCall3: {}, // job id -> true once its reading reached call 3
  notes: {}, // job id -> notes typed and not saved yet
  judging: {}, // job id -> the length asked of the judge, while it answers
  openEvidence: {}, // job id and requirement -> true while all the bullets that earn it are shown
  batch: null, // the chip's count once reading starts: {total, done, ids, finished, complete, failed}
  problems: "", // the jobs the list could not show, said once
  pageModes: { pages: "hybrid", call2: "opus" }, // how pages are built; the server's replaces this
  mode: "add", // what the bar does: "add" or "search"
  addText: "", // what was typed in Add mode, kept while searching
  page: "jobs", // the page shown: "jobs", "profile" or "stats"
  lastMain: "jobs" // Stats sits outside the stack; the stack keeps showing the page Stats was opened from
};

var root = document.documentElement;
var app = document.getElementById("app");
var list = document.getElementById("list");
var detailPane = document.getElementById("detail-pane");
var strip = document.getElementById("status-strip");

// ---------- theme and view transitions ----------
function isDark() {
  return root.getAttribute("data-theme") === "dark" || (!root.getAttribute("data-theme") && window.matchMedia("(prefers-color-scheme: dark)").matches);
}
// Rows visible in the list's own scroll area (and in the window) become transition layers; the rest stay in the
// list's picture.
function nameVisibleRows() {
  var area = list.getBoundingClientRect();
  var top = Math.max(area.top, 0);
  var bottom = Math.min(area.bottom, window.innerHeight);
  document.querySelectorAll("#list .row[data-key]").forEach(function (row) {
    var box = row.getBoundingClientRect();
    var onScreen = box.height > 0 && box.bottom > top && box.top < bottom;
    row.style.viewTransitionName = onScreen ? "row-" + row.getAttribute("data-key") : "none";
  });
}
// Rows change height between the table and the compact list, so the job just opened or closed is brought back
// into the list's view.
function keepRowInView(id) {
  var row = list.querySelector('[data-open="' + CSS.escape(id) + '"]');
  if (row && row.offsetParent !== null) row.scrollIntoView({ block: "nearest" });
}
function transition(update) {
  if (!REDUCED && document.startViewTransition) {
    nameVisibleRows();
    var started = document.startViewTransition(update);
    // A transition started while another runs cancels the first; its `ready` then rejects, which is expected.
    started.ready.catch(function () {});
    return;
  }
  update();
}
document.getElementById("theme-toggle").addEventListener("click", function () {
  var dark = isDark();
  transition(function () { root.setAttribute("data-theme", dark ? "light" : "dark"); });
});

// ---------- data helpers ----------
function payText(pay) { return pay || "Not stated"; }
var SPONS_TEXT = { yes: "Sponsors", no: "No sponsorship", unstated: "Not stated" };
function sponsHtml(value) { return '<span class="spons spons-' + esc(value) + '"><span class="dot"></span>' + SPONS_TEXT[value] + "</span>"; }
function chipHtml(status) { return '<span class="chip st-' + esc(status) + '"><span class="dot"></span>' + STATUS_LABEL[status] + "</span>"; }
function findJob(id) { return jobs.filter(function (job) { return job.id === id; })[0]; }

// ---------- the list ----------
var BUSY_MATCH = '<span class="match"><span class="match-num muted">…</span><span class="bar busy"><i></i></span></span>';
function matchCell(job) {
  if (state.reading[job.id]) return BUSY_MATCH;
  if (job.fetching) return '<span class="skel" style="width:70px"></span>';
  if (!job.read) return '<button type="button" class="read-link" data-read="' + esc(job.id) + '">Read</button>';
  if (job.refused) return '<span class="match-num muted" title="' + esc(job.refused) + '">No page</span>';
  if (!hasMatch(job)) return BUSY_MATCH;
  var value = matchOf(job);
  return '<span class="match ' + tone(value) + '"><span class="match-num">' + Math.round(value * 100) + '%</span><span class="bar"><i data-w="' + (value * 100).toFixed(1) + '"></i></span></span>';
}

function visibleJobs() {
  var text = state.text.trim().toLowerCase();
  var result = jobs.filter(function (job) {
    if (text && (job.title + " " + job.company).toLowerCase().indexOf(text) < 0) return false;
    if (state.sponsor && job.spons !== "yes") return false;
    if (state.remote && job.work_mode !== "Remote") return false;
    if (state.statusFilter && job.status !== state.statusFilter) return false;
    return true;
  });
  result.sort(function (a, b) {
    if (state.sort === "date") return a.added < b.added ? 1 : a.added > b.added ? -1 : 0;
    return (hasMatch(b) ? matchOf(b) : -1) - (hasMatch(a) ? matchOf(a) : -1);
  });
  return result;
}

function renderStrip() {
  var html = '<button type="button" data-status="" aria-pressed="' + (state.statusFilter === null) + '">All <span class="count">' + jobs.length + "</span></button>";
  STATUSES.forEach(function (status) {
    var count = jobs.filter(function (job) { return job.status === status; }).length;
    html += '<button type="button" class="st-' + status + '" data-status="' + status + '" aria-pressed="' + (state.statusFilter === status) + '"><span class="dot" style="color:var(--chip-fg)"></span>' + STATUS_LABEL[status] + ' <span class="count">' + count + "</span></button>";
  });
  strip.innerHTML = html;
  renderNote();
  var active = jobs.filter(function (job) { return job.status === "applied" || job.status === "interview"; }).length;
  document.getElementById("head-count").textContent = jobs.length + " jobs, " + active + " in progress";
}

// The chip says what waits to be read and what reading it costs before any is spent: the unread postings, and the
// read ones whose hybrid page still wants call 3. The server runs call 1 for `schedule.call1` postings at a time and
// call 2 for `schedule.batch` postings at once (Opus's batch in one call, or on Jev as many postings as its requests in
// flight allow), the two overlapping; each call at its median time, by the model that answers it now. The slower stage
// sets the pace, plus one call of the other and a page build. Once reading starts the chip shows one count of how many
// are done, however the readings were started.
var schedule = { call1: 3, batch: 5, call3: 6, classifier: "jev" };
function costText(waiting) {
  var unread = waiting.filter(function (job) { return !job.read; });
  var needingCall1 = unread.filter(function (job) { return job.needs.indexOf("requirements") >= 0; }).length;
  return secondsText(readingTime(needingCall1, unread.length, waiting.length - unread.length));
}
// Seconds to read `postings` postings, `needingCall1` of which need call 1 (every one needs call 2), and to have the
// model choose the projects for `choosing` read ones. Call 3 runs `schedule.call3` at a time: six on Jev (as many as its
// requests in flight), three on Opus; `schedule.classifier` is call 3's model. In hybrid mode Jev's call 3 runs beside
// the others from the start; Opus's adds one call at the end, since a low match waits for it after its page. The read
// ones' call 3s run beside the others' calls.
function readingTime(needingCall1, postings, choosing) {
  var call3 = callSeconds.projects;
  var choices = choosing ? Math.ceil(choosing / schedule.call3) * call3 + 1.5 : 0;
  if (!postings) return choices;
  var call1 = callSeconds.requirements, call2 = callSeconds.mapping;
  var firstStage = Math.ceil(needingCall1 / schedule.call1) * call1, secondStage = Math.ceil(postings / schedule.batch) * call2;
  var time = Math.max(firstStage + call2, (needingCall1 ? call1 : 0) + secondStage) + 1.5;
  if (state.pageModes.pages === "hybrid") {
    time = schedule.classifier === "jev" ? Math.max(time, Math.ceil(postings / schedule.call3) * call3 + 1.5) : time + call3;
  }
  return Math.max(time, choices);
}
function secondsText(seconds) {
  seconds = Math.max(1, Math.round(seconds)); // Jev's calls take under a second
  return seconds < 90 ? "about " + seconds + " s" : "about " + Math.round(seconds / 60) + " min";
}
// What Read all reads: the unread jobs, and the read ones whose hybrid page still wants call 3 (`/api/read` runs it).
function jobsToRead() {
  return jobs.filter(function (job) { return (!job.read || job.choice_needed) && !state.reading[job.id] && !job.fetching; });
}
function renderNote() {
  var note = document.getElementById("unread-note");
  var waiting = jobsToRead();
  var count = waiting.length;
  var batch = state.batch;
  var wasHidden = note.hidden;
  if (!count && !batch) {
    if (wasHidden) return;
    if (REDUCED) { note.hidden = true; return; }
    note.animate([{ opacity: 1, transform: "none" }, { opacity: 0, transform: "translateX(6px)" }], { duration: 200, easing: "ease-in" }).onfinish = function () {
      if (!state.batch && !jobsToRead().length) note.hidden = true;
    };
    return;
  }
  var kind = batch ? (batch.complete ? "done" : "busy") : "unread";
  var label = { unread: "Read all", busy: "Reading", done: "All read" }[kind];
  var textEl = document.getElementById("chip-text");
  var countEl = document.getElementById("chip-count");
  if (textEl.textContent !== label) {
    textEl.textContent = label;
    if (!REDUCED && !wasHidden) textEl.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 200, easing: EASE });
  }
  countEl.hidden = kind === "done";
  countEl.className = "chip-count" + (kind === "unread" ? " badge" : "");
  countEl.textContent = kind === "unread" ? String(count) : kind === "busy" ? batch.done + "/" + batch.total : "";
  if (kind === "unread") document.getElementById("chip-cost").textContent = costText(waiting);
  note.classList.toggle("is-busy", kind === "busy");
  note.classList.toggle("is-done", kind === "done");
  note.setAttribute("aria-label",
    kind === "unread" ? "Read all " + count + " postings, " + costText(waiting)
      : kind === "busy" ? "Reading, " + batch.done + " of " + batch.total + " done" : "All read");
  if (kind === "busy") note.setAttribute("aria-busy", "true"); else note.removeAttribute("aria-busy");
  note.hidden = false;
  if (!REDUCED && wasHidden) {
    note.animate([{ opacity: 0, transform: "translateX(-6px)" }, { opacity: 1, transform: "none" }], { duration: 240, easing: EASE });
  }
}

function rowHtml(job, index) {
  var stagger = "--i:" + index;
  if (job.fetching) {
    return '<div class="row" data-key="' + esc(job.id) + '" aria-busy="true" style="' + stagger + ';cursor:default">' +
      '<span class="cell-role"><span class="skel" style="width:60%;margin-bottom:7px"></span><span class="skel" style="width:35%"></span></span>' +
      '<span class="cell cell-hide"><span class="skel" style="width:70%"></span></span><span class="cell cell-hide"><span class="skel" style="width:60%"></span></span>' +
      '<span class="cell cell-hide"><span class="skel" style="width:60%"></span></span><span class="cell-match"><span class="skel" style="width:70px"></span></span>' +
      '<span class="cell cell-hide"></span><span class="cell cell-hide cell-date"></span></div>';
  }
  return '<div class="row" role="button" tabindex="0" data-key="' + esc(job.id) + '" data-open="' + esc(job.id) + '" aria-current="' + (state.openId === job.id) + '" style="' + stagger + '">' +
    '<span class="cell-role"><span class="role-title">' + esc(job.title) + '</span><span class="role-sub">' + esc(job.company) + "</span></span>" +
    '<span class="cell cell-hide">' + esc(job.place) + '<span class="muted"> · ' + esc(job.work_mode) + "</span></span>" +
    '<span class="cell cell-hide mono">' + esc(payText(job.pay)) + "</span>" +
    '<span class="cell cell-hide">' + sponsHtml(job.spons) + "</span>" +
    '<span class="cell-match">' + matchCell(job) + "</span>" +
    '<span class="cell cell-hide" data-chip="' + esc(job.id) + '">' + chipHtml(job.status) + "</span>" +
    '<span class="cell cell-hide cell-date mono">' + dateText(job.added) + "</span>" +
    '<span class="cell-meta"><span data-chip-meta="' + esc(job.id) + '">' + chipHtml(job.status) + '</span><span class="role-sub">' + esc(job.place) + " · " + esc(payText(job.pay)) + "</span></span>" +
    "</div>";
}

function messageRow(html) {
  return '<div class="row" style="display:block;color:var(--muted);cursor:default">' + html + "</div>";
}

function renderList(options) {
  var slide = options && options.slide;
  var jobsNow = visibleJobs();
  var before = {};
  // Rows slide to their new places after a filter or sort; positions are compared only while the list is on screen.
  var animate = slide && !REDUCED && list.offsetParent !== null;
  if (animate) list.querySelectorAll("[data-key]").forEach(function (el) { before[el.getAttribute("data-key")] = el.getBoundingClientRect(); });
  var head = '<div class="row-head" role="presentation"><span>Role</span><span>Location</span><span>Salary</span><span>Sponsorship</span><span>Match</span><span>Status</span><span class="cell-date">Added</span></div>';
  list.innerHTML = head + (jobsNow.length ? jobsNow.map(rowHtml).join("") : messageRow(
    jobs.length ? "No job matches these filters." : "No jobs yet. Paste a job URL or the posting's text above."));
  if (state.firstRender && !REDUCED) list.querySelectorAll(".row").forEach(function (el) { el.classList.add("enter"); });
  if (animate) {
    list.querySelectorAll("[data-key]").forEach(function (el) {
      var first = before[el.getAttribute("data-key")];
      var last = el.getBoundingClientRect();
      if (!first) { el.animate([{ opacity: 0, transform: "translateY(-6px)" }, { opacity: 1, transform: "none" }], { duration: 300, easing: EASE }); return; }
      var dy = first.top - last.top;
      if (Math.abs(dy) > 1) el.animate([{ transform: "translateY(" + dy + "px)" }, { transform: "none" }], { duration: 340, easing: EASE });
    });
  }
  state.firstRender = false;
  fillBars(list);
  renderStrip();
}

// ---------- the open job ----------
// The capability a requirement asks for, each alternative named ("a or b").
function capText(req) {
  if (req.caps.length) return req.caps.join(" or ");
  return req.conditions && req.conditions.length ? req.conditions.join(", ") : "matched by its words";
}
// What earns the credit: each bullet as its project and its text; the first shown, the rest behind a button that
// opens and closes them. None when the education or skills lines earn it.
function evidenceHtml(entry, partial) {
  return '<div class="evidence"><span class="evidence-project">' + (partial ? "Partly · " : "") + esc(entry.project) + "</span>" +
    '<span class="evidence-text">' + esc(entry.text) + "</span></div>";
}
function byHtml(req, key, index) {
  if (req.state === "missing") return "Nothing on the page shows this.";
  if (!req.by.length) return (req.state === "met" ? "Met" : "Partly met") + " by the education or skills lines.";
  var partial = req.state === "partial";
  var html = evidenceHtml(req.by[0], partial);
  if (req.by.length < 2) return html;
  var open = Boolean(state.openEvidence[key]);
  return html + '<div class="evidence-more" id="evidence-' + index + '"' + (open ? "" : " hidden") + ">" +
    req.by.slice(1).map(function (entry) { return evidenceHtml(entry, partial); }).join("") + "</div>" +
    '<button type="button" class="more-evidence" data-more-evidence="' + esc(key) + '" data-hidden-count="' + (req.by.length - 1) + '" aria-controls="evidence-' + index +
    '" aria-expanded="' + open + '">' + (open ? "Show less" : "+" + (req.by.length - 1) + " more") + "</button>";
}
function reqsHtml(detail) {
  var reqs = detail.requirements || [];
  var groups = [["Required", reqs.filter(function (r) { return r.required; })], ["Preferred", reqs.filter(function (r) { return !r.required; })]];
  var index = 0;
  return groups.map(function (group) {
    if (!group[1].length) return "";
    return '<div class="req-group">' + group[0] + "</div>" + group[1].map(function (req) {
      var capTitle = ' title="' + esc(capText(req) + (req.caps.length && req.conditions && req.conditions.length ? " · needs " + req.conditions.join(", ") : "")) + '"';
      index += 1;
      return '<div class="req ' + esc(req.state) + ' enter" style="--i:' + index + '"><span class="req-mark" role="img" aria-label="' + esc(req.state) + '"></span>' +
        '<span class="req-name">' + esc(req.name) + '</span><span class="req-cap"' + capTitle + ">" + esc(capText(req)) + '</span><div class="req-by">' +
        byHtml(req, detail.id + "\n" + req.name, index) + "</div></div>";
    }).join("");
  }).join("") + ((detail.unmapped || []).length ? '<p class="req-note">Not in the mapping, so met by words alone: ' + esc(detail.unmapped.join("; ")) + "</p>" : "");
}

// A run of "\t" splits a line; what follows it is right-aligned (the template's dates and places).
function runsHtml(runs) {
  var parts = [[]];
  runs.forEach(function (run) { if (run[0] === "\t") parts.push([]); else parts[parts.length - 1].push(run); });
  return parts.map(function (part) {
    return "<span>" + part.map(function (run) { return run[1] ? "<b>" + esc(run[0]) + "</b>" : esc(run[0]); }).join("") + "</span>";
  }).join("");
}
// The page as the Word file prints it: its paragraphs in order, the first two being the name and the contact line.
function paperHtml(detail) {
  var html = "", inList = false;
  (detail.printed || []).forEach(function (paragraph, index) {
    if (paragraph.bullet) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += "<li>" + runsHtml(paragraph.runs) + "</li>";
      return;
    }
    if (inList) { html += "</ul>"; inList = false; }
    var text = paragraph.runs.map(function (run) { return run[0] === "\t" ? " " : run[0]; }).join("");
    if (index === 0) html += '<div class="p-name">' + esc(text) + "</div>";
    else if (index === 1) html += '<div class="p-contact">' + esc(text) + "</div>";
    else if (paragraph.heading) html += '<div class="p-h">' + esc(text) + "</div>";
    else html += '<div class="p-line">' + runsHtml(paragraph.runs) + "</div>";
  });
  if (inList) html += "</ul>";
  return '<div class="paper-fit"><div class="paper" role="figure" aria-label="Page preview">' + html + "</div></div>";
}
// The page is laid out at a letter page's size (816 px wide) and scaled to its box.
var PAGE_WIDTH = 816;
var paperObserver = window.ResizeObserver ? new ResizeObserver(function (entries) {
  entries.forEach(function (entry) { fitPaper(entry.target); });
}) : null;
function fitPaper(box) {
  var paper = box.querySelector(".paper");
  if (paper && box.clientWidth) paper.style.setProperty("--paper-scale", String(box.clientWidth / PAGE_WIDTH));
}
function fitPapers(scope) {
  if (paperObserver) paperObserver.disconnect();
  scope.querySelectorAll(".paper-fit").forEach(function (box) {
    fitPaper(box);
    if (paperObserver) paperObserver.observe(box);
  });
}

var STAGE_LABEL = { call1: "Reading the requirements", call2: "Matching your capabilities", building: "Building the page", call3: "Choosing the projects" };
function readingPanelHtml() {
  var steps = state.pageModes.pages === "hybrid" ? ["call1", "call2", "building", "call3"] : ["call1", "call2", "building"];
  return '<section class="panel reading"><h3>Reading the posting</h3><div class="bar"><i id="read-progress" style="transform:scaleX(.04)"></i></div>' +
    '<div class="steps">' + steps.map(function (stage) {
      return '<span class="step" data-stage="' + stage + '">' + STAGE_LABEL[stage] + "</span>";
    }).join("") + "</div></section>";
}
// Reading's usual time slides open on the button on hover or keyboard focus, as the judge's do.
function notReadHtml(job) {
  var time = costText([job]);
  return '<section class="panel"><h3>Not read yet</h3>' +
    '<button type="button" class="btn btn-primary" data-read="' + esc(job.id) + '" aria-label="Read this posting, ' + time + '">Read this posting' +
    '<span class="cost" aria-hidden="true">' + time + "</span></button></section>";
}

function detailBodyHtml(job) {
  var detail = details[job.id] || {};
  // Requirements that screen candidates out (citizenship, clearance, sponsorship refused, years asked), above all.
  var blockers = (job.blockers || []).length ? '<p class="blockers" role="note"><b>Deal-breakers</b>' +
    job.blockers.map(function (item) { return "<span>" + esc(item) + "</span>"; }).join("") + "</p>" : "";
  var left;
  if (state.reading[job.id]) {
    left = readingPanelHtml();
  } else if (!job.read) {
    left = notReadHtml(job);
  } else if (job.refused) {
    left = '<section class="panel"><h3>No page</h3><p style="margin:0 0 12px;color:var(--ink-2)">The engine did not build a page for this posting: ' + esc(job.refused) + "</p>" +
      '<button type="button" class="btn" data-rebuild>Try again</button></section>';
  } else if (!hasMatch(job)) {
    left = '<section class="panel"><h3>Building the page</h3><div class="bar busy"><i></i></div></section>';
  } else {
    left = matchPanelHtml(job, detail) +
      choiceNeededHtml(job) +
      judgeHtml(job, detail) +
      '<section class="panel"><h3>Requirements</h3><div class="reqs">' + reqsHtml(detail) + "</div></section>";
  }
  var right = factsPanelHtml(job, detail) + statusPanelHtml(job, detail);
  if (hasMatch(job) && !state.reading[job.id]) right += pagePanelHtml(job, detail);
  return blockers + '<div class="detail-grid"><div class="col">' + left + '</div><div class="col">' + right + "</div></div>";
}
function matchPanelHtml(job, detail) {
  var value = matchOf(job);
  var reqs = detail.requirements || [];
  var required = reqs.filter(function (r) { return r.required; });
  var tally = job.tally;
  // An edited page keeps the measures of the page as built before the edits, which may be the one from the model's
  // projects.
  var engineNote = detail.engine ? "edited by you · before your edits " + Math.round(detail.engine.coverage * 100) + "%" : required.length + " required, " + (reqs.length - required.length) + " preferred";
  // A page from the model's projects says so in one line under the score: whose projects, the engine's own page, how
  // many of the chosen projects are on the page when not all are, why.
  var choice = detail.choice;
  var why = "";
  if (job.projects_by === "model" && choice) {
    // Which model chose them, and why when another answered in place of the one asked.
    var by = detail.choice_by || {};
    var notes = by.fallback ? [esc(by.fallback)] : [];
    if (detail.engine_match != null) notes.push("the engine's own page " + Math.round(detail.engine_match * 100) + "%");
    // The selector places the chosen projects' bullets under the page's line budget and weak-project gate, so a chosen
    // project may be left out. A page built before pages recorded `placed` has none until it is built again.
    if (choice.placed && choice.placed.length < choice.projects.length) notes.push(choice.placed.length + " of " + choice.projects.length + " placed");
    why = '<p class="choice-why"><b>Projects chosen by ' + esc(by.by || "the model") + "</b>" + (notes.length ? '<span class="muted">' + notes.join(" · ") + "</span>" : "") +
      (choice.why ? "<br>" + esc(choice.why) : "") + "</p>";
  }
  return '<section class="panel"><h3>Match <span class="aside">' + engineNote + "</span></h3>" +
    '<div class="score ' + tone(value) + '"><div class="score-num"><span data-count="' + Math.round(value * 100) + '">' + Math.round(value * 100) + "</span><small>%</small></div>" +
    '<div class="score-side"><div class="bar"><i data-w="' + (value * 100).toFixed(1) + '"></i></div><div class="tally"><span class="t-met"><b>' + tally.met + '</b> met</span><span class="t-partial"><b>' + tally.partial + '</b> partial</span><span class="t-missing"><b>' + tally.missing + "</b> missing</span></div></div></div>" + why + "</section>";
}
function factsPanelHtml(job, detail) {
  var tiers = (detail.pay_tiers || []).length > 1 ? '<div class="tiers">' + detail.pay_tiers.map(function (tier) {
    return "<span>" + esc(tier.label || "") + '</span><span class="mono">' + esc(tier.pay || "") + "</span>";
  }).join("") + "</div>" : "";
  return '<section class="panel"><h3>Facts</h3><dl class="facts">' +
    '<div><dt>Salary</dt><dd class="mono">' + esc(payText(job.pay)) + tiers + "</dd></div>" +
    "<div><dt>Sponsorship</dt><dd>" + sponsHtml(job.spons) + "</dd></div>" +
    "<div><dt>Work mode</dt><dd>" + esc(job.work_mode) + "</dd></div>" +
    "<div><dt>Level</dt><dd>" + esc(job.level) + "</dd></div>" +
    "<div><dt>Years asked</dt><dd>" + esc(job.years) + "</dd></div>" +
    "<div><dt>Degree</dt><dd>" + esc(job.degree) + "</dd></div>" +
    '<div class="wide"><dt>Location</dt><dd>' + esc(job.place_full || job.place) + "</dd></div></dl></section>";
}
function statusPanelHtml(job, detail) {
  var statusButtons = STATUSES.map(function (status) {
    return '<button type="button" class="st-' + status + '" data-set-status="' + status + '" aria-pressed="' + (job.status === status) + '">' + STATUS_LABEL[status] + "</button>";
  }).join("");
  var notes = state.notes[job.id] !== undefined ? state.notes[job.id] : detail.notes || "";
  return '<section class="panel"><h3>Status</h3><div class="status-picker">' + statusButtons + "</div>" +
    '<label class="muted" for="notes-' + esc(job.id) + '" style="display:block;margin-top:14px;font-size:12px">Notes</label>' +
    '<textarea class="notes" id="notes-' + esc(job.id) + '" data-notes="' + esc(job.id) + '" placeholder="Recruiter, referral, what to follow up on">' + esc(notes) + "</textarea>" +
    '<div class="remove-row"><button type="button" class="link-danger" data-remove' + (state.reading[job.id] ? " disabled" : "") + ">Remove from list</button></div></section>";
}
function pagePanelHtml(job, detail) {
  return '<section class="panel"><h3>The page' + (job.page_current ? "" : ' <span class="aside">being built again</span>') + '</h3><div class="paper-wrap">' + paperHtml(detail) + "</div>" +
    '<div class="actions page-actions" style="margin-top:12px"><a class="btn btn-primary" href="/api/jobs/' + encodeURIComponent(job.id) + '/word" download>Download Word</a><button type="button" class="btn" data-rebuild>Rebuild</button>' + readAgainHtml() + "</div>" +
    editsHtml(detail.edits || []) + "</section>";
}

// Hybrid mode wants call 3 for this page (its engine match is low) and none is saved: one button makes it.
function choiceNeededHtml(job) {
  if (!job.choice_needed || state.reading[job.id]) return "";
  var time = secondsText(callSeconds.projects);
  return '<section class="panel"><h3>Projects <span class="aside">hybrid</span></h3><p style="margin:0 0 12px;color:var(--ink-2)">The engine\'s match is under 60%, where the model\'s choice of projects was judged better. The model has not chosen them for this job yet.</p>' +
    '<button type="button" class="btn" data-choose aria-label="Pick the projects, ' + time + '">Pick the projects<span class="cost" aria-hidden="true">' + time + "</span></button></section>";
}
// Rebuild uses the saved answers; Read again makes every model call again, then the page.
function readAgainHtml() {
  var time = secondsText(readingTime(1, 1, 0));
  return '<button type="button" class="btn" data-read-again aria-label="Read again: every model call made again, ' + time + '">Read again<span class="cost" aria-hidden="true">' + time + "</span></button>";
}

function levelsLink(company) {
  var slug = company.toLowerCase().replace(/&/g, "and").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  return "https://www.levels.fyi/companies/" + slug + "/salaries";
}
function renderDetail(options) {
  var job = findJob(state.openId);
  if (!job) { detailPane.innerHTML = ""; return; }
  // Drawn again for the job already shown (an event, a saved change): the scroll, the focus, the caret and an open
  // Rename form are kept, and nothing enters again.
  var shown = detailPane.getAttribute("data-job") === job.id && detailPane.querySelector(".detail-body");
  var place = shown && !(options && options.swap) ? keepPlace() : null;
  detailPane.setAttribute("data-job", job.id);
  detailPane.innerHTML =
    '<div class="pane-head"><button type="button" class="back" data-close>' +
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg>All jobs</button>' +
    '<div class="title-row"><h2>' + esc(job.title) + '</h2><button type="button" class="icon-btn close-job" data-close aria-label="Close this job">' +
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg></button></div>' +
    '<div class="pane-sub"><span>' + esc(job.company) + "</span><span>" + esc(job.place) + " · " + esc(job.work_mode) + "</span>" +
    (/^https?:\/\//i.test(job.url || "") ? '<a href="' + esc(job.url) + '" target="_blank" rel="noopener noreferrer">Posting ↗</a>' : "") +
    // A pasted posting names no company, so there is no levels.fyi page to link.
    (job.company_known ? '<a href="' + esc(levelsLink(job.company)) + '" target="_blank" rel="noopener noreferrer">Salaries on levels.fyi ↗</a>' : "") +
    '<button type="button" class="link-btn" data-edit-names>' + (job.company_known ? "Rename" : "Add company") + "</button></div>" +
    namesFormHtml(job) + "</div>" +
    '<div class="pane-body detail-body' + (options && options.swap && !REDUCED ? " swap" : "") + '">' + detailBodyHtml(job) + "</div>";
  if (REDUCED || place || (options && options.afterTransition)) detailPane.querySelectorAll(".enter").forEach(function (el) { el.classList.remove("enter"); });
  fitPapers(detailPane);
  if (state.reading[job.id]) showStage(job.id, true);
  if (place) restorePlace(place);
  if (!(options && options.afterTransition)) detailEffects();
}
function keepPlace() {
  var body = detailPane.querySelector(".detail-body");
  var active = document.activeElement;
  var inside = active && active !== detailPane && detailPane.contains(active);
  var form = detailPane.querySelector("form[data-names]");
  return {
    scroll: body.scrollTop,
    windowScroll: window.scrollY,
    focus: inside ? focusKey(active) : null,
    caret: inside && typeof active.selectionStart === "number" ? [active.selectionStart, active.selectionEnd] : null,
    names: form && !form.hidden ? [form.elements.title.value, form.elements.company.value] : null
  };
}
// A selector that finds the same control after a redraw: its id, or its tag with its data attributes and name.
function focusKey(el) {
  if (el.id) return "#" + CSS.escape(el.id);
  var key = el.tagName.toLowerCase();
  Array.prototype.forEach.call(el.attributes, function (attribute) {
    if (attribute.name.indexOf("data-") === 0 || attribute.name === "name") key += "[" + attribute.name + '="' + CSS.escape(attribute.value) + '"]';
  });
  return key;
}
function restorePlace(place) {
  detailPane.querySelector(".detail-body").scrollTop = place.scroll;
  if (window.scrollY !== place.windowScroll) window.scrollTo(0, place.windowScroll);
  var form = detailPane.querySelector("form[data-names]");
  if (place.names && form) {
    form.hidden = false;
    form.elements.title.value = place.names[0];
    form.elements.company.value = place.names[1];
  }
  var control = place.focus && detailPane.querySelector(place.focus);
  if (!control) return;
  control.focus({ preventScroll: true });
  if (place.caret && control.setSelectionRange) control.setSelectionRange(place.caret[0], place.caret[1]);
}
function detailEffects() {
  fillBars(detailPane);
  countUp(detailPane);
}

function toggleEvidence(button) {
  var more = document.getElementById(button.getAttribute("aria-controls"));
  var row = button.closest(".req");
  if (!more || !row) return;
  var open = button.getAttribute("aria-expanded") !== "true";
  var below = [];
  for (var next = row.nextElementSibling; next; next = next.nextElementSibling) below.push(next);
  var before = below.map(function (el) { return el.getBoundingClientRect().top; });
  var key = button.getAttribute("data-more-evidence");
  if (open) state.openEvidence[key] = true; else delete state.openEvidence[key];
  button.setAttribute("aria-expanded", String(open));
  button.textContent = open ? "Show less" : "+" + button.getAttribute("data-hidden-count") + " more";
  more.hidden = !open;
  if (REDUCED) return;
  if (open) more.animate([{ opacity: 0, transform: "translateY(-4px)" }, { opacity: 1, transform: "none" }], { duration: 220, easing: EASE });
  slideFrom(below, before, 260);
}

function markSelected() {
  list.querySelectorAll(".row[data-open]").forEach(function (row) {
    row.setAttribute("aria-current", String(row.getAttribute("data-open") === state.openId));
  });
}

// A job's full record: its requirements with the bullets that earn them, the printed page, notes, the judge's answer.
// The summary fields it carries replace the list's copy.
function keepDetail(id, detail) {
  details[id] = detail;
  var job = findJob(id);
  if (job) Object.keys(detail).forEach(function (key) { job[key] = detail[key]; });
}
function loadDetail(id) {
  return api("/api/jobs/" + encodeURIComponent(id)).then(function (detail) {
    keepDetail(id, detail);
    // The server says whether the judge is being asked; a "judged" event missed while away would leave it waiting.
    if (detail.judging) state.judging[id] = detail.judging; else delete state.judging[id];
    return detail;
  });
}

// A click opens the split; a click on another job swaps what the right side shows; a click on the open job closes.
// The job's record is fetched first (a local request), so the split opens on the finished job.
var opening = null;
// A job's record is fetched before the click: when the pointer rests on its row for 65 ms, when the row is pressed,
// or when it takes the keyboard's focus; it is kept. The click then opens the job at once from what is kept and asks
// the server again behind it, drawing the job again only if the answer changed. A kept record is dropped when an
// event says its job changed. (The fetch was a third of the time from the click to the job moving.)
var fetching = {};
function prefetch(id) {
  if (!id || details[id] || fetching[id]) return;
  var forget = function () { delete fetching[id]; };
  fetching[id] = loadDetail(id);
  fetching[id].then(forget, forget);
}
function openJob(id) {
  if (state.openId === id) { closeJob(); return; }
  if (opening === id) return;
  if (details[id] && !fetching[id]) {
    var kept = JSON.stringify(details[id]);
    showJob(id);
    loadDetail(id).then(function (fresh) { if (state.openId === id && JSON.stringify(fresh) !== kept) renderDetail(); }, function () {});
    return;
  }
  opening = id;
  (fetching[id] || loadDetail(id)).then(function () { if (opening === id) showJob(id); }, function (error) { toast(error.message); })
    .then(function () { if (opening === id) opening = null; });
}
// Opening and closing a job move the page's own elements by transform and opacity, which the compositor runs: the
// rows on screen glide to their places in the narrowed or widened list, the list's buttons slide to theirs, and the
// job slides in from the right or leaves to it. A view transition did this with pictures of the page; capturing
// them at a desktop width cost a 33 to 50 ms frame at the start of every opening, and the same change made without
// the pictures stays at 16.7 ms a frame.
var MOVE = { duration: 460, easing: EASE };
// The parts that change place when the list narrows or widens, by a key that survives the list being drawn again.
function movingParts() {
  var parts = {};
  list.querySelectorAll(".row[data-key]").forEach(function (row) { parts["row " + row.getAttribute("data-key")] = row; });
  document.querySelectorAll(".add-bar .btn-primary, .filter-menu, .sort-menu").forEach(function (el, index) { parts["control " + index] = el; });
  return parts;
}
function placesNow() {
  var parts = movingParts(), places = {};
  Object.keys(parts).forEach(function (key) {
    var box = parts[key].getBoundingClientRect();
    if (box.height) places[key] = box;
  });
  return places;
}
// Each part on screen now is moved back to where it was and glides to its new place; a row that was not shown
// before fades in. Parts outside the list's view, or the window's, are left alone.
function glide(before) {
  var parts = movingParts();
  var shown = list.getBoundingClientRect();
  Object.keys(parts).forEach(function (key) {
    var el = parts[key], last = el.getBoundingClientRect();
    var view = key.indexOf("row ") === 0 ? shown : { top: 0, bottom: window.innerHeight };
    if (!last.height || last.bottom < view.top || last.top > view.bottom) return;
    var first = before[key];
    if (!first) { el.animate([{ opacity: 0 }, { opacity: 1 }], MOVE); return; }
    var dx = first.left - last.left, dy = first.top - last.top;
    if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
    el.animate([{ transform: "translate(" + dx + "px, " + dy + "px)" }, { transform: "none" }], MOVE);
  });
}
// A still copy of the job, fixed where it is, to leave while the page behind it changes. Its rows are set to the
// job's own, so its header and body keep their places, and its body keeps its scroll.
function leavingCopy() {
  var head = detailPane.firstElementChild, body = detailPane.querySelector(".detail-body");
  if (!head || !body) return null;
  var box = detailPane.getBoundingClientRect(), bodyBox = body.getBoundingClientRect();
  var copy = detailPane.cloneNode(true);
  copy.removeAttribute("id");
  copy.querySelectorAll("[id]").forEach(function (el) { el.removeAttribute("id"); });
  copy.classList.add("leaving");
  copy.setAttribute("aria-hidden", "true");
  copy.inert = true;
  copy.style.cssText = "left:" + box.left + "px;top:" + box.top + "px;width:" + box.width + "px;height:" + box.height + "px;" +
    "grid-template-rows:" + (bodyBox.top - box.top) + "px " + bodyBox.height + "px";
  document.body.appendChild(copy);
  copy.querySelector(".detail-body").scrollTop = body.scrollTop;
  return copy;
}
function showJob(id) {
  if (state.openId) {
    state.openId = id;
    markSelected();
    renderDetail({ swap: true });
    return;
  }
  var before = REDUCED ? null : placesNow();
  state.openId = id;
  app.setAttribute("data-open", "true");
  markSelected();
  renderDetail({ afterTransition: true });
  keepRowInView(id);
  if (before) {
    glide(before);
    // The bars fill and the score counts up once the job has arrived.
    detailPane.animate([{ opacity: 0, transform: "translateX(64px)" }, { opacity: 1, transform: "none" }], { duration: 500, easing: EASE })
      .finished.then(detailEffects, function () {});
  } else {
    detailEffects();
  }
  var top = document.querySelector(".workspace").getBoundingClientRect().top;
  if (top < 0) window.scrollTo({ top: window.scrollY + top - 12, behavior: REDUCED ? "auto" : "smooth" });
}
function closeJob() { closeSplit(false); }
// The job leaves one full screen width to the right on the list's curve and time: at least as far as the list grows,
// so it stays ahead of the list's edge. `relist` draws the list again (a job was removed from it).
function closeSplit(relist) {
  var id = state.openId;
  var before = REDUCED ? null : placesNow();
  var leaving = before ? leavingCopy() : null;
  state.openId = null;
  app.setAttribute("data-open", "false");
  detailPane.innerHTML = "";
  if (paperObserver) paperObserver.disconnect();
  if (relist) renderList(); else markSelected();
  keepRowInView(id);
  if (before) glide(before);
  if (leaving) {
    var gone = function () { leaving.remove(); };
    leaving.animate([{ transform: "none" }, { transform: "translateX(100vw)" }], MOVE).finished.then(gone, gone);
  }
  var row = list.querySelector('[data-open="' + CSS.escape(id) + '"]');
  if (row) row.focus({ preventScroll: true });
}

// ---------- reading ----------
// The server reads (call 1 for three postings at a time; call 2, by the Matching mode as the reading starts, on Jev as
// each posting is ready, never batched, and on Opus, Jev's fallback included, in batches of up to five; in hybrid mode
// call 3 six at a time on Jev, three on Opus) and pushes each step as an event; `state.reading[id]` is the step now:
// "queued", "call1", "call2", "call3" or "building".
function countIntoBatch(id) {
  if (!state.batch || state.batch.complete) state.batch = { total: 0, done: 0, ids: {}, finished: {} };
  if (state.batch.ids[id]) return;
  state.batch.ids[id] = true;
  state.batch.total += 1;
}
// Read what the job lacks: an unread one, or a read one whose hybrid page still wants call 3.
function readJob(id) {
  var job = findJob(id);
  if (!job || (job.read && !job.choice_needed) || state.reading[id]) return;
  startReading([id]);
}
function readAgain(id) {
  if (!findJob(id) || state.reading[id]) return;
  state.readingAgain[id] = true;
  markQueued([id]);
  api("/api/jobs/" + encodeURIComponent(id) + "/read-again", { method: "POST" }).catch(function (error) { readFinished(id, error.message); });
}
function startReading(ids) {
  markQueued(ids);
  var request = ids.length === 1
    ? api("/api/jobs/" + encodeURIComponent(ids[0]) + "/read", { method: "POST" })
    : api("/api/read", { method: "POST", body: { ids: ids } });
  request.catch(function (error) { ids.forEach(function (id) { readFinished(id, error.message); }); });
}

// Asked for and not started yet: each job counts into the chip and shows as queued until the server's first event.
function markQueued(ids) {
  ids.forEach(function (id) {
    countIntoBatch(id);
    state.reading[id] = "queued";
    refreshMatchCell(id);
    if (state.openId === id) renderDetail();
  });
  renderStrip();
}

// The bar moves toward the end of the current step over that step's median time, on the compositor (transform). In
// hybrid mode the page is built, then call 3 runs if its match is low, then the page is built again.
var STAGE_ORDER = ["queued", "call1", "call2", "building", "call3"];
function stageTarget(stage, id) {
  var hybrid = state.pageModes.pages === "hybrid";
  if (stage === "call1") return [hybrid ? .4 : .5, callSeconds.requirements];
  if (stage === "call2") return [hybrid ? .68 : .92, callSeconds.mapping];
  if (stage === "building") return [hybrid && !state.sawCall3[id] ? .74 : 1, .5];
  if (stage === "call3") return [.97, callSeconds.projects];
  return [.04, 0];
}
function stageStart(stage, id) {
  var starts = state.pageModes.pages === "hybrid" ? { call2: .4, building: state.sawCall3[id] ? .97 : .68, call3: .74 } : { call2: .5, building: .92 };
  return starts[stage] || .04;
}
function showStage(id, fresh) {
  if (state.openId !== id) return;
  var stage = state.reading[id];
  var panel = detailPane.querySelector(".panel.reading");
  if (!panel) { renderDetail(); return; }
  var at = stage === "building" && state.sawCall3[id] ? STAGE_ORDER.length : STAGE_ORDER.indexOf(stage);
  var job = findJob(id);
  // The calls this reading makes: every one on Read again, else what the job lacks (a read job only wants call 3). A
  // stage that arrives anyway (a Read again started in another tab) is shown as it runs.
  var needs = state.readingAgain[id] || !job ? ["requirements", "mapping"] : job.needs;
  var skipsCall1 = stage !== "call1" && needs.indexOf("requirements") < 0;
  var skipsCall2 = stage !== "call2" && needs.indexOf("mapping") < 0;
  panel.querySelectorAll(".step").forEach(function (step) {
    var position = STAGE_ORDER.indexOf(step.getAttribute("data-stage"));
    var done = position < at || (position === 1 && skipsCall1) || (position === 2 && skipsCall2);
    step.className = "step" + (done ? " done" : position === at ? " active" : "");
  });
  var bar = document.getElementById("read-progress");
  if (!bar) return;
  var target = stageTarget(stage, id);
  if (fresh) {
    bar.style.transition = "none";
    bar.style.transform = "scaleX(" + stageStart(stage, id) + ")";
  }
  afterPaint(function () {
    bar.style.transition = REDUCED ? "none" : "transform " + target[1] + "s cubic-bezier(.3,.6,.45,1)";
    bar.style.transform = "scaleX(" + target[0] + ")";
  });
}

// A finish is counted once per job by the chip's own record, not by `state.reading`: a list refresh that lands
// between the server clearing a job's stage and its "read" event arriving has already removed it from there.
function readFinished(id, failure, calls) {
  if (state.openId !== id) delete details[id];
  var wasReading = id in state.reading;
  delete state.reading[id];
  delete state.readingAgain[id];
  delete state.sawCall3[id];
  var counts = state.batch && state.batch.ids[id] && !state.batch.finished[id];
  if (!wasReading && !counts && !failure) return;
  if (counts) {
    state.batch.finished[id] = true;
    state.batch.done += 1;
    if (failure) state.batch.failed = true;
    if (state.batch.done >= state.batch.total) {
      // The last one lands: "All read" for a moment, then the chip leaves; after a failure it goes straight back to
      // what is still unread.
      var finished = state.batch;
      finished.complete = true;
      if (finished.failed) state.batch = null;
      else setTimeout(function () { if (state.batch === finished) { state.batch = null; renderNote(); } }, 1000);
    }
  }
  refreshJobs().then(function () {
    var job = findJob(id);
    reloadOpenJob(id);
    if (!job) return;
    if (failure) toast("Reading " + job.company + " stopped: " + failure);
    else if (job.refused) toast("Read " + job.company + "; the engine built no page: " + job.refused);
    else if (calls && calls.length === 1 && calls[0] === "projects" && job.projects_by === "model") toast("The model chose the projects for " + job.company + ": " + Math.round(matchOf(job) * 100) + "% match");
    else toast("Read " + job.company + ": " + reqsOf(job).length + " requirements, " + Math.round(matchOf(job) * 100) + "% match");
  });
}
// The open job drawn again from its record fetched anew, or from the copy kept when the fetch fails, so the list's
// newer summary still reaches it.
function reloadOpenJob(id) {
  if (state.openId !== id) return;
  loadDetail(id).then(function () { renderDetail(); }, function () { renderDetail(); });
}
function refreshMatchCell(id) {
  var cell = list.querySelector('[data-key="' + CSS.escape(id) + '"] .cell-match');
  var job = findJob(id);
  if (cell && job) cell.innerHTML = matchCell(job);
}

// ---------- ask the judge ----------
// One model call reads the posting, the page and each requirement's credit, and answers brief or detailed: how
// relevant you are, how well the page is aligned, the gaps, better-fitting roles. The cost is stated before the
// call; the last answer is kept with its date, so asking again is a choice. `state.judging[id]` is the length asked.
var SPARK = '<svg class="icon-spark" width="14" height="14" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M12 0C12 6.63 17.37 12 24 12C17.37 12 12 17.37 12 24C12 17.37 6.63 12 0 12C6.63 12 12 6.63 12 0Z"/></svg>';
var VERDICT_LABEL = { strong: "Strong fit", moderate: "Moderate fit", weak: "Weak fit" };
// Two equal actions; each one's usual time slides open on hover or keyboard focus (no time on a phone).
function judgeButtons(again, secondsByLength) {
  var button = function (level, label) {
    var time = secondsText(secondsByLength[level]);
    // The time is in the name read out, whether or not it is open on screen.
    return '<button type="button" class="btn" data-judge="' + level + '" aria-label="' + label + ", " + time + '">' + SPARK + label +
      '<span class="cost" aria-hidden="true">' + time + "</span></button>";
  };
  return '<div class="actions">' + button("brief", again ? "Ask again, brief" : "Brief") +
    button("detailed", again ? "Ask again, detailed" : "Detailed") + "</div>";
}
function listHtml(items, name, note) {
  return "<ul>" + items.map(function (item) {
    return "<li><b>" + esc(item[name]) + "</b>" + (item[note] ? " " + esc(item[note]) : "") + "</li>";
  }).join("") + "</ul>";
}
function judgeHtml(job, detail) {
  var seconds = detail.judge_seconds;
  var head = '<section class="panel judge"><h3>Ask the judge</h3>';
  var asked = state.judging[job.id];
  if (asked) {
    return head + '<div class="judge-wait"><div class="bar busy"><i></i></div><p class="muted">Reading the posting and the page for a ' + esc(asked) + " answer.</p></div></section>";
  }
  var kept = detail.judge;
  if (!kept) {
    return head + judgeButtons(false, seconds) + "</section>";
  }
  var answer = kept.answer;
  return head +
    '<div class="judge-verdict"><span class="verdict ' + esc(answer.verdict) + '">' + VERDICT_LABEL[answer.verdict] + "</span><p>" + esc(answer.summary) + "</p></div>" +
    '<dl class="judge-answer"><dt>Relevance</dt><dd>' + esc(answer.relevance) + "</dd><dt>Alignment</dt><dd>" + esc(answer.alignment) + "</dd>" +
    (answer.gaps.length ? "<dt>Gaps</dt><dd>" + listHtml(answer.gaps, "gap", "note") + "</dd>" : "") +
    "<dt>Better-fit roles</dt><dd>" + (answer.better_fits.length ? listHtml(answer.better_fits, "role", "why") : "None: this role fits.") + "</dd></dl>" +
    proposalsHtml(kept.proposals || []) +
    '<p class="judge-meta">' + (kept.level === "detailed" ? "Detailed" : "Brief") + " answer · " + dateText(kept.at.slice(0, 10)) + " · " + Math.round(kept.seconds) + " s" +
    (kept.page_changed ? " · the page has been built again since" : "") + "</p>" + judgeButtons(true, seconds) + "</section>";
}
// Only the panel is drawn again, so the job page keeps its scroll and its other boxes stay still.
function showJudge(id) {
  if (state.openId !== id) return;
  var panel = detailPane.querySelector(".panel.judge");
  var job = findJob(id);
  if (!panel || !job) return;
  panel.outerHTML = judgeHtml(job, details[id] || {});
  var fresh = detailPane.querySelector(".panel.judge");
  if (fresh && !REDUCED) fresh.animate([{ opacity: .4 }, { opacity: 1 }], { duration: 220, easing: EASE });
}
function askJudge(id, level) {
  if (!id || state.judging[id]) return;
  state.judging[id] = level;
  showJudge(id);
  api("/api/jobs/" + encodeURIComponent(id) + "/judge", { method: "POST", body: { level: level } }).catch(function (error) {
    delete state.judging[id];
    showJudge(id);
    toast(error.message);
  });
}
function judgeFinished(id, failure) {
  delete state.judging[id];
  var company = (findJob(id) || {}).company || "the job";
  if (failure) { showJudge(id); toast("The judge did not answer for " + company + ": " + failure); return; }
  loadDetail(id).then(function () {
    showJudge(id);
    if (state.openId !== id) { toast("The judge answered for " + company); return; }
    // The answer is taller than the wait; the browser keeps the boxes below still, which can carry the answer's
    // top out of view, so it is brought back.
    // (On a phone the page scrolls, not the job's box, so the view's top is the window's.)
    var panel = detailPane.querySelector(".panel.judge");
    var viewTop = Math.max(0, detailPane.querySelector(".detail-body").getBoundingClientRect().top);
    if (panel && panel.getBoundingClientRect().top < viewTop) {
      panel.scrollIntoView({ block: "start", behavior: REDUCED ? "auto" : "smooth" });
    }
  }, function () { showJudge(id); });
}


// ---------- the judge's proposed page edits ----------
// Each proposal moves bullets that already exist; code checked it fits the page. Yes applies it and builds the page
// again (no model call); No dismisses it. Both are recorded with the page's measures before and after.
function changeLines(change) {
  return (change.remove || []).map(function (entry) {
    return '<div class="change-line minus"><span class="sign" role="img" aria-label="remove">−</span><span><b>' + esc(entry.project) + "</b> <q>" + esc(entry.text) + "</q></span></div>";
  }).concat((change.add || []).map(function (entry) {
    return '<div class="change-line plus"><span class="sign" role="img" aria-label="add">+</span><span><b>' + esc(entry.project) + "</b> <q>" + esc(entry.text) + "</q></span></div>";
  })).join("") + (change.why ? '<p class="change-why">' + esc(change.why) + "</p>" : "");
}
var PROPOSAL_STATE = { applied: "Applied", dismissed: "Dismissed" };
function proposalsHtml(proposals) {
  if (!proposals.length) return "";
  return '<div class="proposals"><div class="mini-head">Suggested changes to the page</div>' + proposals.map(function (proposal, index) {
    var ask = proposal.state === "open"
      ? '<div class="change-ask">Apply this change? <button type="button" class="btn btn-primary" data-proposal="' + index + '" data-accept="yes">Yes</button><button type="button" class="btn" data-proposal="' + index + '" data-accept="no">No</button></div>'
      : proposal.state === "unfit"
        ? '<p class="change-state">Does not fit the page as it is now: ' + esc(proposal.problem || "") + "</p>"
        : '<p class="change-state">' + (PROPOSAL_STATE[proposal.state] || esc(proposal.state)) + "</p>";
    return '<div class="change ' + esc(proposal.state) + '">' + changeLines(proposal) + ask + "</div>";
  }).join("") + "</div>";
}
function editsHtml(edits) {
  if (!edits.length) return "";
  return '<div class="edits"><div class="mini-head">Edited by you</div>' + edits.map(function (edit, index) {
    return '<div class="change' + (edit.applies ? "" : " stale") + '">' + changeLines(edit) +
      (edit.applies ? "" : '<p class="change-state">No longer applies to the engine\'s page: ' + esc(edit.problem || "") + "</p>") +
      '<div class="change-ask"><button type="button" class="btn" data-undo-edit="' + index + '">Undo</button></div></div>';
  }).join("") + "</div>";
}
function afterPageChange(id, detail, message) {
  keepDetail(id, detail);
  if (state.openId === id) renderDetail();
  renderList();
  toast(message);
}
// A change the server answers with the job's page built again (`path` under the job's own): the button waits, then
// the page is drawn and `message` said, a text or a function of the answer; a refusal gives the button back.
function changePage(id, button, path, body, message) {
  button.disabled = true;
  api("/api/jobs/" + encodeURIComponent(id) + path, { method: "POST", body: body }).then(function (detail) {
    afterPageChange(id, detail, typeof message === "function" ? message(detail) : message);
  }, function (error) { button.disabled = false; toast(error.message); });
}
function decideProposal(id, index, accept, button) {
  changePage(id, button, "/proposals/" + index, { accept: accept }, accept ? "Applied; the page was built again" : "Dismissed");
}
function undoEdit(id, index, button) {
  changePage(id, button, "/edits/" + index + "/undo", undefined, "Undone; the page was built again");
}

// ---------- the server's list and its events ----------
function applyJobs(data) {
  var adding = jobs.filter(function (job) { return job.fetching; });
  jobs = adding.concat(data.jobs.map(function (job) {
    var known = details[job.id];
    return known ? Object.assign(known, job) : job;
  }));
  callSeconds = data.seconds || callSeconds;
  schedule = data.schedule || schedule;
  if (data.modes) { state.pageModes = data.modes; renderPageModes(); }
  state.reading = Object.assign({}, data.stages || {});
  var problems = (data.problems || []).join("; ");
  if (problems && problems !== state.problems) toast("Not shown: " + problems);
  state.problems = problems;
}
// One list request at a time: events arriving while one is out (a rebuild after a library edit sends one per job)
// share a single follow-up request.
var refreshing = null, refreshAgain = false;
function refreshJobs() {
  if (refreshing) {
    refreshAgain = true;
    return refreshing;
  }
  refreshing = api("/api/jobs").then(function (data) {
    applyJobs(data);
    renderList({ slide: true });
  }, function (error) { toast(error.message); }).then(function () {
    refreshing = null;
    if (refreshAgain) { refreshAgain = false; return refreshJobs(); }
  });
  return refreshing;
}
function handleEvent(event) {
  if (event.type === "modes") {
    state.pageModes = event.modes;
    renderPageModes();
    refreshJobs().then(function () { if (state.openId) reloadOpenJob(state.openId); });
    return;
  }
  if (event.type === "stage") {
    var starting = !state.reading[event.id];
    state.reading[event.id] = event.stage;
    if (event.stage === "call3") state.sawCall3[event.id] = true;
    if (starting) { countIntoBatch(event.id); refreshMatchCell(event.id); renderStrip(); }
    showStage(event.id, starting);
    return;
  }
  if (event.type === "read") { readFinished(event.id, null, event.calls); return; }
  if (event.type === "judging") { state.judging[event.id] = event.level; showJudge(event.id); return; }
  if (event.type === "judged") { judgeFinished(event.id, null); return; }
  if (event.type === "judge-failed") { judgeFinished(event.id, event.message); return; }
  if (event.type === "read-failed") { readFinished(event.id, event.message); return; }
  if (event.type === "built" || event.type === "build-failed") {
    if (state.openId !== event.id) delete details[event.id];
    if (event.type === "build-failed") toast("The page for " + ((findJob(event.id) || {}).company || "a job") + " was not rebuilt: " + event.message);
    refreshJobs().then(function () {
      reloadOpenJob(event.id);
    });
  }
}
// The browser reconnects the stream by itself after a server restart or a sleep, but what was sent meanwhile is
// lost, so each reconnection catches up. A short break passes quietly; one past five seconds is said once.
var connectedOnce = false, lostTimer = null;
function connectEvents() {
  if (!window.EventSource) return;
  var source = new EventSource("/api/events");
  source.onmessage = function (message) { handleEvent(JSON.parse(message.data)); };
  source.onopen = function () {
    clearTimeout(lostTimer);
    lostTimer = null;
    if (connectedOnce) catchUp();
    connectedOnce = true;
  };
  source.onerror = function () {
    if (lostTimer === null) lostTimer = setTimeout(function () { toast("The server is not answering; the page will catch up when it does"); }, 5000);
    // A refused stream (not a dropped one) is not retried by the browser; it is opened again here.
    if (source.readyState === EventSource.CLOSED) setTimeout(connectEvents, 5000);
  };
}
// After a break: the list and the open job read again. A reading that ended meanwhile is finished as its event
// would have finished it (read, if the list now says so); a judge's answer that came meanwhile shows with the job.
function catchUp() {
  refreshJobs().then(function () {
    var batch = state.batch;
    if (batch && !batch.complete) {
      Object.keys(batch.ids).forEach(function (id) {
        if (batch.finished[id] || state.reading[id]) return;
        var job = findJob(id);
        readFinished(id, job && job.read ? null : "it stopped while the page was not connected");
      });
    }
    if (state.openId) reloadOpenJob(state.openId);
  });
}

// ---------- adding a job ----------
// The fetch and the facts need no model: a row shows while the posting is fetched (a URL, up to 25 s).
var counter = 0;
function addJob(input) {
  var value = input.trim();
  if (!value) { toast("Paste a job URL or the posting's text first"); return; }
  counter += 1;
  var placeholder = { id: "adding-" + counter, fetching: true, title: "", company: "", place: "", work_mode: "", added: todayIso(), status: "added", read: false, reqs: [] };
  jobs.unshift(placeholder);
  setSort("date");
  renderList({ slide: true });
  api("/api/jobs", { method: "POST", body: { source: value } }).then(function (data) {
    jobs = jobs.filter(function (job) { return job !== placeholder; });
    var known = findJob(data.job.id);
    if (known) Object.assign(known, data.job); else jobs.unshift(data.job);
    renderList({ slide: true });
    toast(data.created ? "Added " + data.job.company + ". Facts read from the posting; press Read for the match" : data.job.company + ": " + data.job.title + " is already in your list");
  }, function (error) {
    jobs = jobs.filter(function (job) { return job !== placeholder; });
    renderList({ slide: true });
    toast(error.message);
    // What was pasted is put back, so a failed fetch does not lose it.
    if (!bar.value && state.mode === "add") {
      var shown = value.replace(/\s+/g, " ").trim();
      if (shown !== value) pasted = { text: value, shown: shown };
      bar.value = shown;
      state.addText = shown;
      syncBar();
    }
  });
}

// ---------- toast ----------
var toastTimer;
function toast(message) {
  var el = document.getElementById("toast");
  el.textContent = message.charAt(0).toUpperCase() + message.slice(1);
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(function () { el.classList.remove("show"); }, 2600);
}

// ---------- the bar: Add or Search ----------
var bar = document.getElementById("add-input");
var barField = document.getElementById("bar-field");
var toolsAdd = document.getElementById("tools-add");
var toolsSearch = document.getElementById("tools-search");
function syncBar() { barField.classList.toggle("has-text", bar.value.length > 0); }
function searchActive() { return Boolean(state.text.trim() || state.sponsor || state.remote); }
function updateSearchDot() { document.getElementById("search-dot").hidden = !(state.mode === "add" && searchActive()); }
function swapTools(leaving, arriving) {
  if (REDUCED) { leaving.hidden = true; arriving.hidden = false; return; }
  leaving.animate([{ opacity: 1, transform: "none" }, { opacity: 0, transform: "translateX(6px)" }], { duration: 120, easing: "ease-in" }).onfinish = function () {
    leaving.hidden = true;
    arriving.hidden = false;
    arriving.animate([{ opacity: 0, transform: "translateX(-6px)" }, { opacity: 1, transform: "none" }], { duration: 220, easing: EASE });
  };
}
// The pill's target is computed from each chip's settled width (padding 8, icon, 6 and the label, padding 11 when
// chosen; padding 8, icon, padding 8 otherwise), so it can travel while the labels are still opening and closing.
var modesEl = document.querySelector(".modes");
var modeIndicator = modesEl.querySelector(".modes-indicator");
function settledChipWidth(chip, chosen) {
  var icon = chip.querySelector("svg").getBoundingClientRect().width;
  return chosen ? 8 + icon + 6 + chip.querySelector(".mode-label").scrollWidth + 11 : 8 + icon + 8;
}
function placeModeIndicator() {
  var x = 3; // the control's padding
  modesEl.querySelectorAll(".mode-chip").forEach(function (chip) {
    var chosen = chip.getAttribute("data-mode") === state.mode;
    var width = settledChipWidth(chip, chosen);
    if (chosen) placePill(modeIndicator, width, x);
    x += width + 2; // the gap between chips
  });
}
placeModeIndicator();
if (document.fonts && document.fonts.ready) document.fonts.ready.then(placeModeIndicator);
// Placed once at load, the pill kept a wrong size when the fonts or the page width were not settled yet (seen on a
// first load in the artifact viewer). It is placed again whenever its row changes size; a still pill does not move.
if (window.ResizeObserver) new ResizeObserver(function () { placeModeIndicator(); }).observe(modesEl);
afterPaint(function () { modeIndicator.classList.remove("still"); });
function setMode(mode, focus) {
  if (mode !== state.mode) {
    if (state.mode === "add") state.addText = bar.value;
    var leaving = state.mode === "add" ? toolsAdd : toolsSearch;
    state.mode = mode;
    app.setAttribute("data-mode", mode);
    document.querySelectorAll(".mode-chip").forEach(function (chip) {
      chip.setAttribute("aria-selected", String(chip.getAttribute("data-mode") === mode));
    });
    placeModeIndicator();
    bar.value = mode === "add" ? state.addText : state.text;
    bar.setAttribute("aria-label", mode === "add" ? "Job URL or posting text" : "Search by role or company");
    closeMenus();
    syncBar();
    updateSearchDot();
    swapTools(leaving, mode === "add" ? toolsAdd : toolsSearch);
  }
  if (focus) bar.focus();
}
document.querySelector(".modes").addEventListener("click", function (event) {
  var chip = event.target.closest(".mode-chip");
  if (chip) setMode(chip.getAttribute("data-mode"), true);
});
bar.addEventListener("input", function () {
  syncBar();
  if (state.mode === "search") { state.text = bar.value; renderList({ slide: true }); } else state.addText = bar.value;
});
document.getElementById("bar-clear").addEventListener("click", function () {
  bar.value = "";
  state.text = "";
  syncBar();
  renderList({ slide: true });
  bar.focus();
});
// The bar is one line, and a one-line field drops line breaks, which the posting's facts and reading depend on
// ("Location:" lines, section headings). A posting pasted in Add mode is kept whole and sent as pasted; the field
// shows it on one line, and editing the field falls back to what it shows.
var pasted = null;
bar.addEventListener("paste", function (event) {
  if (state.mode !== "add" || !event.clipboardData) return;
  var text = event.clipboardData.getData("text");
  if (text.indexOf("\n") < 0) return;
  event.preventDefault();
  var shown = text.replace(/\s+/g, " ").trim();
  pasted = { text: text, shown: shown };
  bar.value = shown;
  state.addText = shown;
  syncBar();
});
document.getElementById("add-form").addEventListener("submit", function (event) {
  event.preventDefault();
  if (state.mode !== "add") return;
  addJob(pasted && pasted.shown === bar.value ? pasted.text : bar.value);
  pasted = null;
  bar.value = "";
  state.addText = "";
  syncBar();
});
// "/" jumps to search from anywhere but a text field; Escape in the search bar returns to Add.
document.addEventListener("keydown", function (event) {
  if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey) return;
  var typing = event.target.closest && event.target.closest("input, textarea, select");
  if (typing || state.page !== "jobs") return;
  event.preventDefault();
  setMode("search", true);
});
document.getElementById("unread-note").addEventListener("click", function () {
  if (state.batch && !state.batch.complete) return;
  var waiting = jobsToRead();
  if (!waiting.length) return;
  state.batch = null;
  startReading(waiting.map(function (job) { return job.id; }));
  renderNote();
});

var filterButton = document.getElementById("filter-button");
var filterMenu = { root: document.querySelector(".filter-menu"), button: filterButton, panel: document.getElementById("filter-panel") };
var sortMenu = { root: document.querySelector(".sort-menu"), button: document.getElementById("sort-button"), panel: document.getElementById("sort-panel") };
var pageModesMenu = { root: document.querySelector(".page-modes-menu"), button: document.getElementById("page-modes-button"), panel: document.getElementById("page-modes-panel") };
var menus = [filterMenu, sortMenu, pageModesMenu];
function setMenu(menu, open) {
  if (open === !menu.panel.hidden) return;
  menu.button.setAttribute("aria-expanded", String(open));
  if (open) {
    menus.forEach(function (other) { if (other !== menu) setMenu(other, false); });
    menu.panel.hidden = false;
    if (!REDUCED) menu.panel.animate([{ opacity: 0, transform: "translateY(-4px) scale(.97)" }, { opacity: 1, transform: "none" }], { duration: 170, easing: EASE });
    return;
  }
  if (REDUCED) { menu.panel.hidden = true; return; }
  menu.panel.animate([{ opacity: 1, transform: "none" }, { opacity: 0, transform: "translateY(-4px) scale(.97)" }], { duration: 130, easing: "ease-in" }).onfinish = function () {
    if (menu.button.getAttribute("aria-expanded") === "false") menu.panel.hidden = true;
  };
}
function closeMenus() { menus.forEach(function (menu) { setMenu(menu, false); }); }
function openMenu() { return menus.filter(function (menu) { return !menu.panel.hidden && menu.button.getAttribute("aria-expanded") === "true"; })[0]; }
menus.forEach(function (menu) {
  menu.button.addEventListener("click", function () { setMenu(menu, menu.panel.hidden); });
});
document.addEventListener("click", function (event) {
  menus.forEach(function (menu) { if (!menu.panel.hidden && !menu.root.contains(event.target)) setMenu(menu, false); });
});
// How pages are built and who matches the requirements (Accuracy or Speed): the server keeps the two
// switches, since they change what it builds and calls. The button names Speed only; Accuracy is the default.
function pageModesText(modes) {
  return (modes.pages === "hybrid" ? "Hybrid" : "Legacy") + (modes.call2 === "jev" ? " · speed" : "");
}
function renderPageModes() {
  var modes = state.pageModes;
  document.getElementById("page-modes-value").textContent = pageModesText(modes);
  document.getElementById("page-modes-button").setAttribute("aria-label", "How pages are built: " + pageModesText(modes));
  document.querySelectorAll("#page-modes-panel [data-pages]").forEach(function (item) {
    item.setAttribute("aria-checked", String(item.getAttribute("data-pages") === modes.pages));
  });
  document.querySelectorAll("#page-modes-panel [data-call2]").forEach(function (item) {
    item.setAttribute("aria-checked", String(item.getAttribute("data-call2") === modes.call2));
  });
}
document.getElementById("page-modes-panel").addEventListener("click", function (event) {
  var item = event.target.closest(".menu-item");
  if (!item || item.getAttribute("aria-checked") === "true") return;
  var body = item.hasAttribute("data-pages") ? { pages: item.getAttribute("data-pages") } : { call2: item.getAttribute("data-call2") };
  // The toast names the mode, then says what its line in the menu says, and what changes now.
  var name = item.querySelector(".item-text > span").textContent;
  var said = item.querySelector("small").textContent;
  var now = body.pages ? "pages are being built again, with no model call" : "applies to postings read from now on, and to Read again";
  setMenu(pageModesMenu, false);
  // The server also sends a "modes" event, which draws the list and the open job again.
  api("/api/modes", { method: "POST", body: body }).then(function (result) {
    state.pageModes = result.modes;
    renderPageModes();
    renderNote();
    toast(name + ": " + said.charAt(0).toLowerCase() + said.slice(1) + "; " + now);
  }, function (error) { toast(error.message); });
});
var SORT_LABEL = { match: "Best match", date: "Newest" };
function setSort(value) {
  state.sort = value;
  document.getElementById("sort-value").textContent = SORT_LABEL[value];
  document.getElementById("sort-button").setAttribute("aria-label", "Sort by: " + SORT_LABEL[value]);
  document.querySelectorAll("#sort-panel .menu-item").forEach(function (item) {
    item.setAttribute("aria-checked", String(item.getAttribute("data-sort") === value));
  });
}
document.getElementById("sort-panel").addEventListener("click", function (event) {
  var item = event.target.closest(".menu-item");
  if (!item) return;
  setSort(item.getAttribute("data-sort"));
  setMenu(sortMenu, false);
  renderList({ slide: true });
});
["sponsor", "remote"].forEach(function (name) {
  document.getElementById("filter-" + name).addEventListener("change", function (event) {
    state[name] = event.target.checked;
    var active = (state.sponsor ? 1 : 0) + (state.remote ? 1 : 0);
    var count = document.getElementById("filter-count");
    count.textContent = String(active);
    count.hidden = !active;
    filterButton.classList.toggle("active", active > 0);
    updateSearchDot();
    filterButton.setAttribute("aria-label", active ? "Filters, " + active + " on" : "Filters");
    renderList({ slide: true });
  });
});
strip.addEventListener("click", function (event) {
  var button = event.target.closest("[data-status]");
  if (!button) return;
  var status = button.getAttribute("data-status") || null;
  state.statusFilter = state.statusFilter === status ? null : status;
  renderList({ slide: true });
});
list.addEventListener("click", function (event) {
  var read = event.target.closest("[data-read]");
  if (read) { event.stopPropagation(); readJob(read.getAttribute("data-read")); return; }
  var row = event.target.closest("[data-open]");
  if (row) openJob(row.getAttribute("data-open"));
});
var restingOn = null, restTimer;
function restOn(row) {
  if (row === restingOn) return;
  restingOn = row;
  clearTimeout(restTimer);
  if (row) restTimer = setTimeout(function () { prefetch(row.getAttribute("data-open")); }, 65);
}
list.addEventListener("pointerover", function (event) { restOn(event.target.closest(".row[data-open]")); });
list.addEventListener("pointerleave", function () { restOn(null); });
list.addEventListener("focusin", function (event) { restOn(event.target.closest(".row[data-open]")); });
list.addEventListener("pointerdown", function (event) {
  var row = event.target.closest(".row[data-open]");
  if (row) prefetch(row.getAttribute("data-open"));
});
list.addEventListener("keydown", function (event) {
  if (event.key !== "Enter" && event.key !== " ") return;
  var row = event.target.closest(".row[data-open]");
  if (row && row === event.target) { event.preventDefault(); openJob(row.getAttribute("data-open")); }
});
function showStatus(job, status) {
  job.status = status;
  detailPane.querySelectorAll("[data-set-status]").forEach(function (button) {
    button.setAttribute("aria-pressed", String(button.getAttribute("data-set-status") === status));
  });
  ["data-chip", "data-chip-meta"].forEach(function (attribute) {
    var cell = list.querySelector("[" + attribute + '="' + CSS.escape(job.id) + '"]');
    if (!cell) return;
    cell.innerHTML = chipHtml(status);
    if (!REDUCED) cell.firstChild.classList.add("pop");
  });
  renderStrip();
}
function rebuild(id, button) {
  changePage(id, button, "/rebuild", undefined, function (detail) { return detail.refused ? "No page: " + detail.refused : "Rebuilt from the saved reading, no model call"; });
}
detailPane.addEventListener("click", function (event) {
  var target = event.target.closest("button");
  if (!target) return;
  if (target.hasAttribute("data-close")) { closeJob(); return; }
  if (target.hasAttribute("data-read")) { readJob(target.getAttribute("data-read")); return; }
  if (target.hasAttribute("data-choose")) { readJob(state.openId); return; }
  if (target.hasAttribute("data-read-again")) { readAgain(state.openId); return; }
  if (target.hasAttribute("data-rebuild")) { rebuild(state.openId, target); return; }
  if (target.hasAttribute("data-judge")) { askJudge(state.openId, target.getAttribute("data-judge")); return; }
  if (target.hasAttribute("data-edit-names")) { showNames(detailPane.querySelector("form[data-names]").hidden); return; }
  if (target.hasAttribute("data-names-cancel")) { showNames(false); return; }
  if (target.hasAttribute("data-remove")) { removeJob(target); return; }
  if (target.hasAttribute("data-proposal")) { decideProposal(state.openId, +target.getAttribute("data-proposal"), target.getAttribute("data-accept") === "yes", target); return; }
  if (target.hasAttribute("data-more-evidence")) { toggleEvidence(target); return; }
  if (target.hasAttribute("data-undo-edit")) { undoEdit(state.openId, +target.getAttribute("data-undo-edit"), target); return; }
  var status = target.getAttribute("data-set-status");
  if (!status) return;
  var job = findJob(state.openId);
  if (!job || job.status === status) return;
  var before = job.status;
  showStatus(job, status);
  api("/api/jobs/" + encodeURIComponent(job.id), { method: "PATCH", body: { status: status } }).then(function () {
    toast("Marked " + STATUS_LABEL[status].toLowerCase());
  }, function (error) { showStatus(job, before); toast("Not saved: " + error.message); });
});
// ---------- renaming and removing a job ----------
// The names given here are kept in the tracker and shown in place of the posting's own (a pasted posting names no
// company); an empty field returns to the posting's. They reach the list, the search, the Word file's name and the
// judge; the posting file stays as it was fetched or pasted.
function namesFormHtml(job) {
  return '<form class="name-edit" data-names hidden>' +
    '<label>Title<input name="title" maxlength="200" value="' + esc(job.title) + '"></label>' +
    '<label>Company<input name="company" maxlength="100" placeholder="Company" value="' + esc(job.company_known ? job.company : "") + '"></label>' +
    '<div class="actions"><button type="submit" class="btn btn-primary">Save</button><button type="button" class="btn" data-names-cancel>Cancel</button></div></form>';
}
function showNames(open) {
  var form = detailPane.querySelector("form[data-names]");
  if (!form) return;
  form.hidden = !open;
  if (!open) return;
  var job = findJob(state.openId);
  var field = form.querySelector(job && !job.company_known ? 'input[name="company"]' : 'input[name="title"]');
  field.focus();
  field.select();
}
function saveNames(form) {
  var id = state.openId;
  var body = { title: form.querySelector('input[name="title"]').value, company: form.querySelector('input[name="company"]').value };
  api("/api/jobs/" + encodeURIComponent(id), { method: "PATCH", body: body }).then(function (data) {
    var job = findJob(id);
    if (job) Object.assign(job, data.job);
    if (details[id]) Object.assign(details[id], data.job);
    if (state.openId === id) renderDetail();
    renderList();
    toast("Saved: " + data.job.title + ", " + data.job.company);
  }, function (error) { toast("Not saved: " + error.message); });
}
detailPane.addEventListener("submit", function (event) {
  var form = event.target.closest("form[data-names]");
  if (!form) return;
  event.preventDefault();
  saveNames(form);
});
// Escape inside the form closes the form, not the job.
detailPane.addEventListener("keydown", function (event) {
  if (event.key !== "Escape" || !event.target.closest("form[data-names]")) return;
  event.stopPropagation();
  showNames(false);
});

// Removing asks for a second click within four seconds; the job's saved files stay, and adding the posting again
// brings it back.
var removeTimer;
function removeJob(button) {
  var id = state.openId;
  if (!button.classList.contains("armed")) {
    button.classList.add("armed");
    button.textContent = "Click again to remove";
    clearTimeout(removeTimer);
    removeTimer = setTimeout(function () { button.classList.remove("armed"); button.textContent = "Remove from list"; }, 4000);
    return;
  }
  clearTimeout(removeTimer);
  button.disabled = true;
  var title = (findJob(id) || {}).title || "the job";
  api("/api/jobs/" + encodeURIComponent(id) + "/remove", { method: "POST" }).then(function () {
    jobs = jobs.filter(function (job) { return job.id !== id; });
    delete details[id];
    closeSplit(true);
    toast("Removed " + title + ". Adding the posting again brings it back");
  }, function (error) { button.disabled = false; toast(error.message); });
}

// Notes are saved 0.7 s after typing stops, and when the box loses focus.
var noteTimers = {};
function saveNotes(id) {
  clearTimeout(noteTimers[id]);
  delete noteTimers[id];
  if (state.notes[id] === undefined) return;
  var text = state.notes[id];
  api("/api/jobs/" + encodeURIComponent(id), { method: "PATCH", body: { notes: text } }).then(function () {
    if (state.notes[id] === text) delete state.notes[id];
    if (details[id]) details[id].notes = text;
  }, function (error) { toast("Notes not saved: " + error.message); });
}
detailPane.addEventListener("input", function (event) {
  var id = event.target.getAttribute && event.target.getAttribute("data-notes");
  if (!id) return;
  state.notes[id] = event.target.value;
  clearTimeout(noteTimers[id]);
  noteTimers[id] = setTimeout(function () { saveNotes(id); }, 700);
});
detailPane.addEventListener("focusout", function (event) {
  var id = event.target.getAttribute && event.target.getAttribute("data-notes");
  if (id && noteTimers[id]) saveNotes(id);
});
document.addEventListener("keydown", function (event) {
  if (event.key !== "Escape") return;
  var menuOpen = openMenu();
  if (menuOpen) { setMenu(menuOpen, false); menuOpen.button.focus(); return; }
  if (state.mode === "search" && document.activeElement === bar) { setMode("add", true); return; }
  if (state.openId && state.page === "jobs") closeJob();
});

// ---------- pages: the menu, or the card stack under 880 px ----------
var pages = document.getElementById("pages");
var indicator = pages.querySelector(".pages-indicator");
var jobsView = document.getElementById("jobs-view");
var profileView = document.getElementById("profile-view");
var statsView = document.getElementById("stats-view");
var statsButton = document.getElementById("stats-button");
var VIEWS = { jobs: jobsView, profile: profileView, stats: statsView };
var compactQuery = window.matchMedia("(max-width: 880px)");
var CARD_BEHIND = "translate(6px, -6px) rotate(5deg)";

function frontPage() { return state.page === "stats" ? state.lastMain : state.page; }
function card(page) { return pages.querySelector('[data-page="' + page + '"]'); }
function syncCards() {
  var compact = app.getAttribute("data-compact") === "true";
  pages.querySelectorAll(".page-card").forEach(function (button) {
    var page = button.getAttribute("data-page");
    var front = page === frontPage();
    button.setAttribute("aria-current", page === state.page ? "page" : "false");
    button.setAttribute("data-front", String(front && page !== "stats"));
    // In the stack the card behind is only an edge; the front card is the one control.
    button.tabIndex = compact && !front ? -1 : 0;
    if (compact && !front) button.setAttribute("aria-hidden", "true"); else button.removeAttribute("aria-hidden");
  });
  statsButton.setAttribute("aria-pressed", String(state.page === "stats"));
}
function placeIndicator() {
  if (app.getAttribute("data-compact") === "true") return;
  var current = card(state.page);
  placePill(indicator, current.getBoundingClientRect().width, current.offsetLeft);
}
function setCompact(animate) {
  var compact = String(compactQuery.matches);
  if (compact === app.getAttribute("data-compact")) return;
  var apply = function () { app.setAttribute("data-compact", compact); syncCards(); indicator.classList.add("still"); placeIndicator(); };
  if (animate) transition(apply); else apply();
  afterPaint(function () { indicator.classList.remove("still"); });
}
function shuffle(leaving, arriving) {
  leaving.animate([
    { transform: "none", zIndex: 2 },
    { transform: "translate(-16px, 24px) rotate(-8deg)", zIndex: 2, offset: .45 },
    { transform: CARD_BEHIND, zIndex: 0 }
  ], { duration: 480, easing: EASE });
  arriving.animate([
    { transform: CARD_BEHIND, zIndex: 0 },
    { transform: "translate(2px, 3px) scale(.97)", zIndex: 0, offset: .45 },
    { transform: "none", zIndex: 1 }
  ], { duration: 480, easing: EASE });
}
// `then` runs once the new page is on screen.
function showPage(page, then) {
  if (page === state.page) { if (then) then(); return; }
  var from = VIEWS[state.page];
  var to = VIEWS[page];
  var compact = app.getAttribute("data-compact") === "true";
  var betweenStackPages = state.page !== "stats" && page !== "stats";
  if (compact && betweenStackPages && !REDUCED) shuffle(card(state.page), card(page));
  if (page !== "stats") state.lastMain = page;
  state.page = page;
  syncCards();
  placeIndicator();
  closeMenus();
  // Profile and Stats are fetched as the old page fades, and drawn before the new one fades in.
  var loading = page === "profile" ? loadProfile() : page === "stats" ? loadStats() : Promise.resolve();
  function arrive() {
    if (to === profileView) showProfile();
    if (to === statsView) renderStats();
    if (then) then();
  }
  function swapViews() {
    if (state.page !== page) return;
    from.hidden = true;
    to.hidden = false;
    arrive();
    if (!REDUCED) to.animate([{ opacity: 0, transform: "translateY(8px)" }, { opacity: 1, transform: "none" }], { duration: 280, easing: EASE });
  }
  var fetched = loading.catch(function (error) { toast(error.message); });
  if (REDUCED) { fetched.then(swapViews); return; }
  // The fade holds its end (fill) until the new page is drawn, then is removed, so the old page never flashes back
  // and shows at full opacity when next opened (or at once, if another switch overtook this one).
  var fade = from.animate([{ opacity: 1, transform: "none" }, { opacity: 0, transform: "translateY(6px)" }], { duration: 160, easing: "ease-in", fill: "forwards" });
  fade.onfinish = function () { fetched.then(function () { swapViews(); fade.cancel(); }); };
}
pages.addEventListener("click", function (event) {
  var button = event.target.closest(".page-card");
  if (!button) return;
  if (app.getAttribute("data-compact") !== "true") { showPage(button.getAttribute("data-page")); return; }
  // The stack: from Stats it returns to its front page; otherwise it shuffles between Jobs and Profile.
  if (state.page === "stats") showPage(state.lastMain);
  else showPage(state.page === "jobs" ? "profile" : "jobs");
});
statsButton.addEventListener("click", function () { showPage(state.page === "stats" ? state.lastMain : "stats"); });

// ---------- Profile and Stats (profile.js, stats.js) ----------
// Stats opens a job, or Profile's coverage, through the page switch here.
connectStats({
  jobs: function () { return jobs; },
  findJob: findJob,
  openJob: function (id) { showPage("jobs", function () { if (state.openId !== id) openJob(id); }); },
  showCoverage: function () { showPage("profile", function () { showSection("coverage"); }); }
});

compactQuery.addEventListener("change", function () { setCompact(true); });
if (window.ResizeObserver) new ResizeObserver(function () { placeIndicator(); }).observe(pages);
setCompact(false);
if (document.fonts && document.fonts.ready) document.fonts.ready.then(placeIndicator);

// The event stream that keeps the page current, then the first list (asked second, so nothing sent in between is
// missed); a reading already running shows in the chip.
connectEvents();
api("/api/jobs").then(function (data) {
  applyJobs(data);
  Object.keys(state.reading).forEach(countIntoBatch);
  renderList();
  Object.keys(state.reading).forEach(function (id) { refreshMatchCell(id); });
}, function (error) {
  list.innerHTML = messageRow("The server did not answer: " + esc(error.message));
});
