// ---------- profile (the library, read-only; `/api/profile`) ----------
import { REDUCED, EASE, api, tone, esc, countText, gapRowHtml, afterPaint, fillBars, countUp, placePill, slideFrom } from "./shared.js";

var profile = null;
export function loadProfile() {
  return api("/api/profile").then(function (data) { profile = data; return data; });
}
var profileBody = document.getElementById("profile-body");
var sectionsEl = document.querySelector(".sections");
var sectionsIndicator = sectionsEl.querySelector(".sections-indicator");
var section = "projects"; // the section shown: "projects", "skills", "education" or "coverage"

function coveredSkills() {
  var covered = {};
  (profile.covered || []).forEach(function (skill) { covered[skill] = true; });
  return covered;
}
function askedBy(skill) { return (profile.asked || {})[skill] || 0; }
function chevron(className) {
  return '<svg class="' + className + '" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';
}
function projectsHtml() {
  var readJobs = profile.read_jobs;
  return '<p class="section-note">Each project, how many of your read jobs\' pages print it, and its bullets with the skills they show.</p>' +
    '<div class="proj-list">' + profile.projects.map(function (project) {
      var used = project.used;
      return '<article class="proj" data-proj="' + esc(project.id) + '">' +
        '<button type="button" class="proj-head" aria-expanded="false" aria-controls="proj-body-' + esc(project.id) + '">' +
        '<span><span class="proj-title">' + esc(project.title) + '</span><span class="proj-facts">' + esc(project.facts) + "</span></span>" +
        '<span class="proj-stats"><span class="proj-use" title="Read jobs whose page prints this project">' +
        (used ? '<span class="bar"><i data-w="' + (used / Math.max(readJobs, 1) * 100).toFixed(1) + '"></i></span><span class="mono">on ' + used + " of " + readJobs + " pages</span>"
              : '<span class="mono">not chosen yet</span>') +
        "</span><span>" + countText(project.bullets.length, "bullet") + "</span></span>" +
        chevron("proj-chevron") + "</button>" +
        '<div class="proj-body" id="proj-body-' + esc(project.id) + '" hidden><ul class="bullets">' + project.bullets.map(function (bullet) {
          return '<li class="bullet' + (bullet.pending ? " pending" : "") + '"><p>' + esc(bullet.text) + '</p><div class="tags">' +
            bullet.skills.map(function (skill) { return '<span class="cap">' + esc(skill) + "</span>"; }).join("") + "</div></li>";
        }).join("") + "</ul></div></article>";
    }).join("") + "</div>";
}
function skillsHtml() {
  return '<p class="section-note">Five rows print on each page, chosen for the posting; a row marked "always printed" prints on every page.</p>' +
    '<section class="panel">' + profile.skill_rows.map(function (row) {
      return '<div class="item-row"><span class="item-name">' + esc(row.title) + (row.fixed ? "<small>always printed</small>" : "") + "</span>" +
        '<span class="tags">' + row.members.map(function (member) { return '<span class="tag">' + esc(member) + "</span>"; }).join("") + "</span></div>";
    }).join("") + "</section>";
}
function educationHtml() {
  return '<p class="section-note">Up to ' + profile.courses_max + " courses print under each degree, chosen for the posting.</p>" +
    profile.degrees.map(function (degree) {
      return '<section class="panel"><h3>' + esc(degree.institution) + ' <span class="aside">' + degree.courses.length + " courses</span></h3>" +
        '<p style="margin:0 0 12px;font-weight:500">' + esc(degree.award) + (degree.dates ? " · " + esc(degree.dates) : "") + "</p>" +
        '<div class="tags">' + degree.courses.map(function (course) { return '<span class="tag">' + esc(course) + "</span>"; }).join("") + "</div></section>";
    }).join("");
}
function coverageHtml() {
  var covered = coveredSkills();
  var all = [];
  profile.domains.forEach(function (domain) { all = all.concat(domain.skills); });
  var have = all.filter(function (skill) { return covered[skill]; }).length;
  var share = have / Math.max(all.length, 1);
  var gaps = all.filter(function (skill) { return !covered[skill] && askedBy(skill); })
    .map(function (skill) { return [skill, askedBy(skill)]; })
    .sort(function (a, b) { return b[1] - a[1]; });
  var most = gaps.length ? gaps[0][1] : 1;
  return '<section class="panel"><h3>Skills with a bullet</h3><div class="score ' + tone(share) + '"><div class="score-num"><span data-count="' + have + '">' + have + "</span><small>/" + all.length + "</small></div>" +
    '<div class="score-side"><div class="bar"><i data-w="' + (share * 100).toFixed(1) + '"></i></div><div class="tally"><span><b>' + (all.length - have) + "</b> skills have no bullet yet</span></div></div></div></section>" +
    '<section class="panel"><h3>Asked for by your jobs, no bullet yet</h3>' +
    (gaps.length ? gaps.map(function (gap) { return gapRowHtml(gap[0], gap[1], most, "tone-low"); }).join("")
      : '<p class="section-note" style="margin:0">None: every skill your read jobs ask for has a bullet.</p>') + "</section>" +
    '<section class="panel"><h3>By area</h3>' + profile.domains.map(function (domain) {
      var inDomain = domain.skills.filter(function (skill) { return covered[skill]; }).length;
      var part = inDomain / Math.max(domain.skills.length, 1);
      var missing = domain.skills.filter(function (skill) { return !covered[skill]; });
      return '<div class="item-row" style="grid-template-columns:minmax(0,1fr)"><div class="domain-head"><span class="item-name">' + esc(domain.name) + "</span>" +
        '<span class="match ' + tone(part) + '"><span class="match-num">' + inDomain + "/" + domain.skills.length + '</span><span class="bar"><i data-w="' + (part * 100).toFixed(1) + '"></i></span></span></div>' +
        (missing.length ? '<div class="tags">' + missing.map(function (skill) { return '<span class="cap missing">' + esc(skill) + "</span>"; }).join("") + "</div>" : "") + "</div>";
    }).join("") + "</section>";
}
var SECTION_HTML = { projects: projectsHtml, skills: skillsHtml, education: educationHtml, coverage: coverageHtml };
function renderProfileHeader() {
  if (!profile) return;
  var bullets = 0;
  profile.projects.forEach(function (project) { bullets += project.bullets.filter(function (b) { return !b.pending; }).length; });
  var skills = 0;
  profile.domains.forEach(function (domain) { skills += domain.skills.length; });
  document.getElementById("profile-sub").textContent = profile.projects.length + " projects · " + bullets + " bullets · " + skills + " skills";
}
function renderSection() {
  if (!profile) return;
  profileBody.innerHTML = SECTION_HTML[section]();
  fillBars(profileBody);
  countUp(profileBody);
}
function placeSectionIndicator() {
  var tab = sectionsEl.querySelector('[data-section="' + section + '"]');
  if (!tab.offsetWidth) return;
  placePill(sectionsIndicator, tab.getBoundingClientRect().width, tab.offsetLeft);
}
// Profile as it arrives on screen: the header, the section shown, and its pill put in place without sliding.
export function showProfile() {
  renderProfileHeader();
  renderSection();
  sectionsIndicator.classList.add("still");
  placeSectionIndicator();
  afterPaint(function () { sectionsIndicator.classList.remove("still"); });
}
export function showSection(chosen) {
  if (chosen === section) return;
  section = chosen;
  sectionsEl.querySelectorAll(".section-tab").forEach(function (tab) {
    tab.setAttribute("aria-selected", String(tab.getAttribute("data-section") === chosen));
  });
  profileBody.setAttribute("aria-labelledby", "section-tab-" + chosen);
  placeSectionIndicator();
  if (REDUCED) { renderSection(); return; }
  profileBody.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 110, easing: "ease-in" }).onfinish = function () {
    renderSection();
    profileBody.scrollTop = 0;
    profileBody.animate([{ opacity: 0, transform: "translateY(6px)" }, { opacity: 1, transform: "none" }], { duration: 240, easing: EASE });
  };
}
// Opening a project: its bullets fade in and the cards below slide to their new places (transform only).
function toggleProject(article) {
  var head = article.querySelector(".proj-head");
  var body = article.querySelector(".proj-body");
  var open = head.getAttribute("aria-expanded") !== "true";
  var cards = [].slice.call(article.parentElement.children);
  var before = cards.map(function (card) { return card.getBoundingClientRect().top; });
  function slideOthers() {
    if (!REDUCED) slideFrom(cards, before, 300);
  }
  head.setAttribute("aria-expanded", String(open));
  if (open) {
    body.hidden = false;
    slideOthers();
    if (!REDUCED) body.animate([{ opacity: 0, transform: "translateY(-4px)" }, { opacity: 1, transform: "none" }], { duration: 260, delay: 60, easing: EASE, fill: "backwards" });
    return;
  }
  if (REDUCED) { body.hidden = true; return; }
  body.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 120, easing: "ease-in" }).onfinish = function () {
    body.hidden = true;
    slideOthers();
  };
}
sectionsEl.addEventListener("click", function (event) {
  var tab = event.target.closest(".section-tab");
  if (tab) showSection(tab.getAttribute("data-section"));
});
profileBody.addEventListener("click", function (event) {
  var head = event.target.closest(".proj-head");
  if (head) toggleProject(head.closest(".proj"));
});
// The pill is placed again whenever its row changes size, which a window resize that moves it also does.
if (window.ResizeObserver) new ResizeObserver(function () { placeSectionIndicator(); }).observe(sectionsEl);
