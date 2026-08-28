"use strict";

// ── channel registry (mirror of feeds.json; colors are presentation-only)
const CHANNELS = [
  { id: "healthtech", label: "Health Tech", abbr: "HLT", color: "#2FBFAD" },
  { id: "storage", label: "Storage Tech", abbr: "STO", color: "#5B8DEF" },
  { id: "fintech", label: "Fintech", abbr: "FIN", color: "#D4A73C" },
  { id: "stocks", label: "Stock Market", abbr: "STK", color: "#FFB020" },
  { id: "energy", label: "Power & Energy", abbr: "PWR", color: "#E8763C" },
  { id: "quantum", label: "Quantum", abbr: "QNT", color: "#9D7BEA" },
  { id: "semis", label: "Semiconductors", abbr: "SMI", color: "#45B8E0" },
  { id: "photonics", label: "Photonics", abbr: "PHO", color: "#F072D6" },
  { id: "compilers", label: "Compilers", abbr: "CMP", color: "#E06377" },
  { id: "broadcast", label: "Broadcast Eng.", abbr: "BCE", color: "#B7C24A" },
  { id: "cyber", label: "Cyber Security", abbr: "SEC", color: "#63D471" },
  { id: "defence", label: "Military Defence", abbr: "DEF", color: "#93A3B8" },
  { id: "chinaai", label: "China AI", abbr: "CNA", color: "#D62839" },
  { id: "math", label: "Mathematics", abbr: "MTH", color: "#8AB4F8" },
];
const CH = Object.fromEntries(CHANNELS.map((c) => [c.id, c]));

// ── lenses: cross-cutting filters by item kind or tag, combinable with a channel
const LENSES = [
  { id: "advisory", label: "ADVISORIES", kind: "advisory" },
  { id: "release", label: "RELEASES", kind: "release" },
  { id: "paper", label: "PAPERS", kind: "paper" },
  { id: "regulatory", label: "REGULATORY", kind: "regulatory" },
  { id: "standards", label: "STANDARDS", tag: "standards" },
  { id: "policy", label: "POLICY", tag: "policy" },
  { id: "hft", label: "HFT", tag: "hft" },
  { id: "quant", label: "QUANT", tag: "quant" },
];
const LENS = Object.fromEntries(LENSES.map((l) => [l.id, l]));
const KIND_LABEL = {
  advisory: "ADVISORY",
  release: "RELEASE",
  weekly: "WEEKLY",
  paper: "PAPER",
  regulatory: "REG",
};

const POLL_MS = 5 * 60 * 1000; // re-fetch JSON every 5 minutes
const state = {
  items: [],
  papers: [],
  active: null,
  lens: null,
  updated: 0,
  seen: new Set(),
};

// ── helpers ────────────────────────────────────────────────────────────
// All feed-derived text goes through textContent — never innerHTML.
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function safeHref(url) {
  try {
    const parsed = new URL(url, location.href);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") {
      return parsed.href;
    }
  } catch (_) { /* fall through */ }
  return null;
}

function relTime(ts) {
  const m = Math.max(0, Math.round((Date.now() - ts) / 60000));
  if (m < 1) return "now";
  if (m < 60) return m + "m";
  const h = Math.floor(m / 60);
  if (h < 48) return h + "h";
  return Math.floor(h / 24) + "d";
}

function link(href, className) {
  const a = el("a", className);
  a.href = href;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  return a;
}

// Items written before kinds/tags existed default to plain news.
function itemKind(item) {
  return typeof item.kind === "string" && KIND_LABEL[item.kind] ? item.kind : "news";
}

function itemTags(item) {
  return Array.isArray(item.tags) ? item.tags.filter((t) => typeof t === "string") : [];
}

function matchesLens(item, lens) {
  if (!lens) return true;
  if (lens.kind) return itemKind(item) === lens.kind;
  if (lens.tag) return itemTags(item).includes(lens.tag);
  return true;
}

function visibleItems() {
  const lens = state.lens ? LENS[state.lens] : null;
  return state.items.filter(
    (i) => (!state.active || i.topic === state.active) && matchesLens(i, lens)
  );
}

// ── URL hash ↔ filter state (so a channel + lens view is linkable) ──────
function readHash() {
  const params = new URLSearchParams(location.hash.slice(1));
  const ch = params.get("ch");
  const lens = params.get("lens");
  state.active = ch && CH[ch] ? ch : null;
  state.lens = lens && LENS[lens] ? lens : null;
}

function syncHash() {
  const parts = [];
  if (state.active) parts.push("ch=" + state.active);
  if (state.lens) parts.push("lens=" + state.lens);
  const hash = parts.length ? "#" + parts.join("&") : "";
  if (location.hash !== hash) {
    history.replaceState(null, "", location.pathname + location.search + hash);
  }
}

// ── rendering ──────────────────────────────────────────────────────────
function renderWire() {
  const wire = document.getElementById("wire");
  wire.replaceChildren();
  const latest = state.items.slice(0, 14);
  for (const dup of [0, 1]) {
    const span = el("span");
    if (dup === 1) span.setAttribute("aria-hidden", "true");
    for (const item of latest) {
      const ch = CH[item.topic];
      if (!ch) continue;
      const wrapper = el("span", "wire-item");
      const abbr = el("span", "abbr", ch.abbr);
      abbr.style.color = ch.color;
      wrapper.append(
        abbr,
        el("span", "sep", " ▸ "),
        document.createTextNode(item.title),
        el("span", "age", " · " + relTime(item.ts))
      );
      span.appendChild(wrapper);
    }
    wire.appendChild(span);
  }
}

function meterNode(count, color) {
  const meter = el("span", "meter");
  meter.setAttribute("aria-hidden", "true");
  for (let i = 0; i < 5; i++) {
    const bar = el("i");
    bar.style.height = 4 + i * 2.5 + "px";
    if (i < Math.min(5, count)) bar.style.background = color;
    meter.appendChild(bar);
  }
  return meter;
}

function renderRack() {
  const rack = document.getElementById("rack");
  rack.replaceChildren();

  const all = el("button", "chip all");
  all.setAttribute("aria-pressed", String(state.active === null));
  all.append(el("span", "abbr", "ALL"));
  all.addEventListener("click", () => setActive(null));
  rack.appendChild(all);

  for (const ch of CHANNELS) {
    const on = state.active === ch.id;
    const recent = state.items.filter(
      (i) => i.topic === ch.id && Date.now() - i.ts < 4 * 3600000
    ).length;
    const chip = el("button", "chip");
    chip.setAttribute("aria-pressed", String(on));
    chip.style.borderColor = on ? ch.color : "rgba(255,255,255,0.10)";
    const abbr = el("span", "abbr", ch.abbr);
    abbr.style.color = ch.color;
    chip.append(abbr, el("span", "label", ch.label), meterNode(recent, ch.color));
    chip.addEventListener("click", () => setActive(on ? null : ch.id));
    rack.appendChild(chip);
  }

  const divider = el("span", "divider");
  divider.setAttribute("aria-hidden", "true");
  rack.appendChild(divider);

  for (const lens of LENSES) {
    const on = state.lens === lens.id;
    const count = state.items.filter(
      (i) => (!state.active || i.topic === state.active) && matchesLens(i, lens)
    ).length;
    const chip = el("button", "chip lens");
    chip.setAttribute("aria-pressed", String(on));
    chip.append(el("span", "abbr", lens.label), el("span", "count", String(count)));
    chip.addEventListener("click", () => setLens(on ? null : lens.id));
    rack.appendChild(chip);
  }
}

function renderFeed() {
  const feed = document.getElementById("feed");
  const emptyNote = document.getElementById("feed-empty");
  const label = document.getElementById("feed-label");
  feed.replaceChildren();

  const visible = visibleItems();
  const parts = [state.active ? CH[state.active].label.toUpperCase() : "ALL CHANNELS"];
  if (state.lens) parts.push(LENS[state.lens].label);
  parts.push(visible.length + " ITEMS");
  label.textContent = parts.join(" · ");
  emptyNote.hidden = visible.length !== 0;

  for (const item of visible.slice(0, 100)) {
    const ch = CH[item.topic];
    const href = safeHref(item.url);
    if (!ch || !href) continue;
    const li = el("li");
    if (!state.seen.has(item.id) && state.seen.size > 0) li.classList.add("flash");
    const a = link(href, "row-link");
    const abbr = el("span", "abbr", ch.abbr);
    abbr.style.color = ch.color;
    abbr.title = ch.label;
    const middle = el("span");
    const kind = itemKind(item);
    if (kind !== "news") {
      middle.appendChild(el("span", "kind kind-" + kind, KIND_LABEL[kind]));
    }
    middle.append(
      el("span", "row-title", item.title),
      el("span", "row-source", " — " + item.source)
    );
    for (const tag of itemTags(item)) {
      middle.appendChild(el("span", "tag", "⌗" + tag));
    }
    a.append(abbr, middle, el("span", "row-age", relTime(item.ts)));
    li.appendChild(a);
    feed.appendChild(li);
  }
}

function renderSurveys() {
  const list = document.getElementById("surveys");
  const emptyNote = document.getElementById("surveys-empty");
  list.replaceChildren();

  const visible = state.active
    ? state.papers.filter((p) => p.topic === state.active)
    : state.papers;
  emptyNote.hidden = visible.length !== 0;

  for (const paper of visible.slice(0, 20)) {
    const ch = CH[paper.topic];
    const href = safeHref(paper.url);
    if (!ch || !href) continue;
    const li = el("li");
    const a = link(href, "paper-link");
    a.appendChild(el("div", "paper-title", paper.title));
    const meta = el("div", "paper-meta");
    const abbr = el("span", "abbr", ch.abbr);
    abbr.style.color = ch.color;
    meta.appendChild(abbr);
    if (paper.authors && paper.authors.length) {
      const names =
        paper.authors.slice(0, 3).join(", ") +
        (paper.authors.length > 3 ? " et al." : "");
      meta.appendChild(el("span", null, names));
    }
    meta.appendChild(el("span", null, relTime(paper.ts)));
    if (typeof paper.citations === "number") {
      meta.appendChild(el("span", "cite-badge", paper.citations + " cites"));
    }
    a.appendChild(meta);
    li.appendChild(a);
    list.appendChild(li);
  }
}

function renderAll() {
  renderWire();
  renderRack();
  renderFeed();
  renderSurveys();

  const updatedLabel = document.getElementById("updated-label");
  if (state.updated) {
    const ageMin = Math.round((Date.now() - state.updated) / 60000);
    updatedLabel.textContent = "Data updated " + relTime(state.updated) + " ago";
    document.body.classList.toggle("stale", ageMin > 60);
    document.getElementById("live-label").textContent =
      ageMin > 60 ? "STALE" : "LIVE";
  }
}

function setActive(id) {
  state.active = id;
  syncHash();
  renderAll();
}

function setLens(id) {
  state.lens = id;
  syncHash();
  renderAll();
}

// ── data loading ───────────────────────────────────────────────────────
async function loadJSON(path) {
  const resp = await fetch(path, { cache: "no-cache" });
  if (!resp.ok) throw new Error(path + ": " + resp.status);
  return resp.json();
}

async function refresh() {
  try {
    const ticker = await loadJSON("data/ticker/all.json");
    const nextSeen = new Set(ticker.items.map((i) => i.id));
    renderPrep(ticker);
    state.seen = nextSeen;
  } catch (err) {
    console.error("ticker load failed:", err);
  }
  try {
    const surveys = await loadJSON("data/surveys/all.json");
    state.papers = surveys.papers || [];
  } catch (err) {
    console.error("surveys load failed:", err);
    state.papers = state.papers || [];
  }
  renderAll();
}

function renderPrep(ticker) {
  state.items = (ticker.items || []).filter(
    (i) => i && typeof i.title === "string" && CH[i.topic]
  );
  state.updated = ticker.updated || 0;
}

// ── clock ──────────────────────────────────────────────────────────────
function tickClock() {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  document.getElementById("clock").textContent =
    pad(now.getHours()) + ":" + pad(now.getMinutes()) + ":" + pad(now.getSeconds());
}

readHash();
window.addEventListener("hashchange", () => { readHash(); renderAll(); });
setInterval(tickClock, 1000);
tickClock();
setInterval(refresh, POLL_MS);
setInterval(renderAll, 60000); // keep relative timestamps fresh
refresh();
