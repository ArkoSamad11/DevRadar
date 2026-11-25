"use strict";

const CATEGORY_NAMES = {
  software: "Software", ml_ai: "ML & AI", data_science: "Data science", systems: "Systems",
  security: "Security", quant: "Quant", operations_research: "Operations research", math: "Math",
};
const DEGREE_NAMES = { bachelors: "Bachelor's", masters: "Master's", mba: "MBA", phd: "PhD" };
const SOURCE_NAMES = { greenhouse: "Greenhouse", ashby: "Ashby", lever: "Lever" };
const GRADUATION_MONTH = /^20\d\d-(0[1-9]|1[0-2])$/;
const PAGE_SIZE = 30;
const MAX_BYTES = 1_000_000;

const $ = (id) => document.getElementById(id);

const state = {
  jobs: [],
  matches: null,
  resume: null,
  view: "all",
  categories: new Set(),
  feed: null,
  request: 0,
};

function element(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children.filter((child) => child !== null && child !== undefined));
  return node;
}

function webLink(url, text) {
  const link = element("a", { textContent: text, target: "_blank", rel: "noopener noreferrer" });
  if (/^https?:\/\//i.test(url)) link.href = url;
  return link;
}

function daysAgo(iso) {
  const days = Math.floor((Date.now() - new Date(iso)) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 60) return `${days} days ago`;
  return `${Math.floor(days / 30)} months ago`;
}

function places(job) {
  const shown = job.locations.slice(0, 3).join("; ");
  const extra = job.locations.length - 3;
  return extra > 0 ? `${shown} and ${extra} more` : shown;
}

function matchesFilters(job) {
  const search = $("search").value.trim().toLowerCase();
  const location = $("location").value.trim().toLowerCase();
  if ($("remote").checked && !job.remote) return false;
  if (state.categories.size && !job.categories.some((c) => state.categories.has(c))) return false;
  if (location && !job.locations.some((l) => l.toLowerCase().includes(location))) return false;
  if (!search) return true;
  const text = [job.title, job.company.name, ...job.locations].join(" ").toLowerCase();
  return search.split(/\s+/).every((word) => text.includes(word));
}

// A feed hands out pages of rows. Local feeds filter the full list held in memory; the search
// feed asks the server and fetches its next page before the reader gets there.
function localFeed(rows) {
  let position = 0;
  return {
    total: rows.length,
    async next() {
      const page = rows.slice(position, position + PAGE_SIZE);
      position += PAGE_SIZE;
      return { rows: page, done: position >= rows.length };
    },
  };
}

function searchFeed() {
  const params = new URLSearchParams({ q: $("search").value.trim(), limit: PAGE_SIZE });
  for (const category of state.categories) params.append("category", category);
  if ($("location").value.trim().length > 1) params.set("location", $("location").value.trim());
  if ($("remote").checked) params.set("remote", "true");

  async function load(cursor) {
    if (cursor) params.set("cursor", cursor);
    const response = await fetch(`/jobs?${params}`);
    if (!response.ok) throw new Error("Search failed. Try again.");
    return response.json();
  }
  let upcoming = load(null);
  upcoming.catch(() => {});
  return {
    total: null,
    async next() {
      const page = await upcoming;
      if (page.next_cursor) {
        upcoming = load(page.next_cursor);
        upcoming.catch(() => {});
      }
      return { rows: page.items.map((job) => ({ job })), done: !page.next_cursor };
    },
  };
}

// Employers sometimes open several postings with the same title in the same place.
function groupRepeats(jobs) {
  const groups = new Map();
  for (const job of jobs) {
    const key = JSON.stringify([job.company.slug, job.title, job.locations]);
    if (groups.has(key)) groups.get(key).others.push(job);
    else groups.set(key, { job, others: [] });
  }
  return [...groups.values()];
}

function tagList(names, className) {
  return element("ul", { className: "tags" }, ...names.map((name) =>
    element("li", { textContent: name, className })));
}

function renderRow({ job, match, others = [] }) {
  const side = element("span", { className: "side" });
  if (match) {
    side.append(element("span", {
      className: "fit number",
      textContent: `Fit ${match.score.toFixed(2)}`,
      title: "Half word similarity, half role fit. Not a probability.",
    }), " · ");
  }
  side.append(daysAgo(job.listed_at));
  const details = [job.company.name, places(job), job.remote ? "Remote" : null,
    job.categories.map((c) => CATEGORY_NAMES[c]).join(", "), SOURCE_NAMES[job.source]];
  const row = element("li", {},
    element("div", { className: "row" }, webLink(job.url, job.title), side),
    element("p", { className: "meta", textContent: details.filter(Boolean).join(" · ") }));
  if (others.length) {
    const links = others.map((other, i) => webLink(other.url, `posting ${i + 2}`));
    row.append(element("p", { className: "quiet repeats",
      textContent: `Posted ${others.length + 1} times: ` }));
    row.lastChild.append(...links.flatMap((link, i) => (i ? [", ", link] : [link])));
  }
  if (match) renderReasons(row, match);
  return row;
}

function monthName(iso) {
  const [year, month] = iso.split("-").map(Number);
  return new Date(year, month - 1).toLocaleDateString("en-US", { month: "short", year: "numeric" });
}

function requirements({ degrees, graduation }) {
  const parts = [];
  if (degrees.length) parts.push(`${degrees.map((d) => DEGREE_NAMES[d]).join(", ")} students`);
  if (graduation) {
    const { earliest, latest } = graduation;
    if (earliest && latest) parts.push(`graduating ${monthName(earliest)} to ${monthName(latest)}`);
    else if (latest) parts.push(`graduating by ${monthName(latest)}`);
    else parts.push(`graduating ${monthName(earliest)} or later`);
  }
  return parts.join(", ");
}

function renderReasons(row, match) {
  const why = element("dl", { className: "why" });
  const add = (label, content) => why.append(
    element("dt", { textContent: label }), element("dd", {}, content));
  const asked = requirements(match);
  if (asked) add("Open to", asked);
  if (match.matched_skills.length) add("You have", tagList(match.matched_skills, ""));
  if (match.missing_skills.length) add("It also asks for", tagList(match.missing_skills, "miss"));
  if (match.matched_terms.length) add("Shared words", match.matched_terms.join(", "));
  row.append(why);
  if (match.mentions_work_authorization) {
    row.append(element("p", { className: "notice",
      textContent: "Mentions citizenship, clearance or U.S. person status. Check that you qualify." }));
  }
  const others = match.similar_postings.length;
  if (others) {
    row.append(element("p", { className: "quiet",
      textContent: `${others} more posting${others > 1 ? "s" : ""} with this title` }));
  }
}

async function showMore() {
  const feed = state.feed;
  if (!feed || feed.busy || feed.done) return;
  feed.busy = true;
  try {
    const { rows, done } = await feed.next();
    if (feed !== state.feed) return;
    $("results").append(...rows.map(renderRow));
    feed.done = done;
    feed.shown = (feed.shown || 0) + rows.length;
    $("end").textContent = done && feed.shown > PAGE_SIZE ? "That's everything." : "";
    if (feed.total === null) {
      $("count").textContent = feed.shown === 0 ? "No internships match."
        : `${feed.shown}${done ? "" : "+"} found in titles and descriptions`;
    }
  } catch (error) {
    if (feed === state.feed) { $("count").textContent = error.message; feed.done = true; }
  } finally {
    feed.busy = false;
  }
  if (feed === state.feed && !feed.done && sentinelVisible()) showMore();
}

function sentinelVisible() {
  return $("sentinel").getBoundingClientRect().top < window.innerHeight + 600;
}

function refresh() {
  const matching = state.view === "matches";
  $("tab-all").setAttribute("aria-selected", String(!matching));
  $("tab-matches").setAttribute("aria-selected", String(matching));
  $("descriptions-option").hidden = matching;

  const onServer = !matching && $("descriptions").checked && $("search").value.trim().length > 1;
  if (onServer) {
    state.feed = searchFeed();
    $("count").textContent = "Searching…";
  } else {
    const rows = matching
      ? state.matches.filter((m) => matchesFilters(m.job)).map((m) => ({ job: m.job, match: m }))
      : groupRepeats(state.jobs.filter(matchesFilters));
    state.feed = localFeed(rows);
    const noun = matching ? "match" : "internship";
    const plural = matching ? "matches" : "internships";
    const total = rows.reduce((sum, row) => sum + 1 + (row.others?.length || 0), 0);
    $("count").textContent = rows.length
      ? `${total.toLocaleString()} ${total === 1 ? noun : plural}${matching ? ", best first" : ", newest first"}`
      : `No ${plural} fit these filters.`;
  }
  $("results").replaceChildren();
  $("end").textContent = "";
  showMore();
}

function show(view) {
  state.view = view;
  refresh();
}

function debounce(action, wait) {
  let timer;
  return () => { clearTimeout(timer); timer = setTimeout(action, wait); };
}

async function loadInternships() {
  try {
    const response = await fetch("/internships");
    if (!response.ok) throw new Error();
    state.jobs = await response.json();
  } catch {
    $("freshness").textContent = "Couldn't load internships. Reload to try again.";
    $("count").textContent = "";
    return;
  }
  fillTerms();
  if (state.view === "all") refresh();
  const total = `${state.jobs.length.toLocaleString()} internships`;
  $("freshness").textContent = total;
  try {
    const health = await (await fetch("/health")).json();
    const minutes = Math.round((Date.now() - new Date(health.last_ingestion_at)) / 60_000);
    const age = minutes < 90 ? `${Math.max(minutes, 1)} min` : `${Math.round(minutes / 60)} hours`;
    $("freshness").textContent = health.status === "stale"
      ? `${total}, last updated ${age} ago (updates are delayed)`
      : `${total}, updated ${age} ago`;
  } catch { /* The list is still usable without its age. */ }
}

function fillTerms() {
  const counts = new Map();
  for (const job of state.jobs) {
    for (const [, season, year] of job.title.matchAll(/(spring|summer|fall|winter)\s*,?\s*(20\d\d)/gi)) {
      const term = `${season.toLowerCase()} ${year}`;
      counts.set(term, (counts.get(term) || 0) + 1);
    }
  }
  const common = [...counts].sort((a, b) => b[1] - a[1]).slice(0, 8).map(([term]) => term);
  const order = ["winter", "spring", "summer", "fall"];
  const key = (term) => { const [s, y] = term.split(" "); return Number(y) * 10 + order.indexOf(s); };
  const chosen = $("term").value;
  $("term").replaceChildren(element("option", { value: "", textContent: "Any" }),
    ...common.sort((a, b) => key(a) - key(b)).map((term) =>
      element("option", { value: term, textContent: term[0].toUpperCase() + term.slice(1) })));
  $("term").value = chosen;
}

function setStatus(message, isError = false) {
  $("resume-status").textContent = message;
  $("resume-status").classList.toggle("error", isError);
}

function chooseFile(file) {
  if (!file) return;
  if (file.type !== "application/pdf" && !/\.pdf$/i.test(file.name)) {
    setStatus("That isn't a PDF. Save your resume as a PDF, or paste its text.", true);
  } else if (file.size > MAX_BYTES) {
    setStatus("That PDF is over 1 MB. Export a smaller one, or paste its text.", true);
  } else {
    state.resume = { body: file, type: "application/pdf", name: file.name };
    matchResume();
  }
}

async function matchResume() {
  if (!state.resume) return;
  const request = ++state.request;
  const params = new URLSearchParams({ kind: $("kind").value, limit: 50 });
  for (const id of ["degree", "term"]) if ($(id).value) params.set(id, $(id).value);
  if (GRADUATION_MONTH.test($("graduation").value)) params.set("graduation", $("graduation").value);
  setStatus("Matching…");
  let result;
  try {
    const response = await fetch(`/match?${params}`, {
      method: "POST", body: state.resume.body, headers: { "Content-Type": state.resume.type },
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(explain(response.status, body.detail));
    result = body;
  } catch (error) {
    if (request === state.request) setStatus(error.message || "Couldn't reach the server. Try again.", true);
    return;
  }
  if (request !== state.request) return;
  state.matches = result.matches;
  renderProfile(result);
  $("tab-matches").disabled = false;
  setStatus(result.confident ? `${result.matches.length} matches found.`
    : "This doesn't clearly fit any tech role we know, so matches are weak.");
  show("matches");
}

function explain(status, detail) {
  if (status === 413) return "That file is over 1 MB.";
  if (status === 429) return "Too many tries. Wait a minute and try again.";
  if (status === 503) return "The server is busy. Try again in a few seconds.";
  if (typeof detail === "string") return `Couldn't use that resume: ${detail}.`;
  return "Couldn't match that resume. Try again.";
}

function renderProfile(result) {
  $("profile").hidden = false;
  $("resume-name").textContent = state.resume.name;
  $("roles").replaceChildren(...result.roles.slice(0, 3).map((role) => element("li", {},
    element("span", { textContent: CATEGORY_NAMES[role.role] }),
    element("span", { className: "number", textContent: `${Math.round(role.score * 100)}%` }),
    element("meter", { value: role.score, min: 0, max: 1, ariaLabel: CATEGORY_NAMES[role.role] }),
    role.evidence.length
      ? element("span", { className: "evidence", textContent: `because of ${role.evidence.join(", ")}` })
      : null)));
  $("skills").replaceChildren(...(result.skills.length ? result.skills : ["None recognized"]).map(
    (skill) => element("li", { textContent: skill })));
}

function clearResume() {
  state.request += 1;
  state.resume = null;
  state.matches = null;
  $("file").value = "";
  $("text").value = "";
  $("profile").hidden = true;
  $("roles").replaceChildren();
  $("skills").replaceChildren();
  $("tab-matches").disabled = true;
  setStatus("Resume removed.");
  show("all");
}

function start() {
  $("categories").append(...Object.entries(CATEGORY_NAMES).map(([slug, name]) => {
    const chip = element("button", { type: "button", textContent: name });
    chip.setAttribute("aria-pressed", "false");
    chip.addEventListener("click", () => {
      const pressed = !state.categories.delete(slug);
      if (pressed) state.categories.add(slug);
      chip.setAttribute("aria-pressed", String(pressed));
      refresh();
    });
    return chip;
  }));

  const soon = debounce(refresh, 120);
  $("search").addEventListener("input", () => ($("descriptions").checked ? debouncedSearch() : soon()));
  $("location").addEventListener("input", soon);
  $("remote").addEventListener("change", refresh);
  $("descriptions").addEventListener("change", refresh);
  const debouncedSearch = debounce(refresh, 350);

  $("tab-all").addEventListener("click", () => show("all"));
  $("tab-matches").addEventListener("click", () => show("matches"));

  const drop = $("drop");
  for (const type of ["dragenter", "dragover"]) {
    drop.addEventListener(type, (event) => { event.preventDefault(); drop.classList.add("over"); });
  }
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (event) => {
    event.preventDefault();
    drop.classList.remove("over");
    chooseFile(event.dataTransfer.files[0]);
  });
  // Dropping outside the box would otherwise navigate away to the file.
  for (const type of ["dragover", "drop"]) window.addEventListener(type, (event) => event.preventDefault());

  $("file").addEventListener("change", (event) => chooseFile(event.target.files[0]));
  $("use-text").addEventListener("click", () => {
    state.resume = { body: $("text").value, type: "text/plain", name: "Pasted text" };
    matchResume();
  });
  for (const id of ["degree", "term", "kind"]) $(id).addEventListener("change", matchResume);
  // Typing a year fires a change for every digit, so half-typed dates are skipped.
  $("graduation").addEventListener("change", ({ target }) => {
    if (!target.value || GRADUATION_MONTH.test(target.value)) matchResume();
  });
  $("clear").addEventListener("click", clearResume);

  new IntersectionObserver((entries) => entries[0].isIntersecting && showMore(),
    { rootMargin: "600px" }).observe($("sentinel"));
  loadInternships();
}

start();
