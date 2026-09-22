// ytalbum web UI. No framework, no build step. All server text goes in via textContent.
"use strict";

const $ = (sel) => document.querySelector(sel);
const PROV = { mb: "MB", yt_music: "YT Music", yt_title: "title", playlist: "playlist", user: "you" };
let state = { albums: [], jobs: [], busy: false };
let waitingFor = null; // job id whose result the results panel is waiting for
let openLog = null; // job id whose full log is expanded
let pollTimer = null;

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) el.append(c instanceof Node ? c : String(c));
  return el;
}

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Ytalbum": "1" },
    body: JSON.stringify(body),
  };
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

async function submit(action, body) {
  try {
    const { job } = await api(`/api/${action}`, body);
    state.jobs.unshift(job);
    renderJobs();
    schedulePoll(300);
    return job.id;
  } catch (e) {
    alert(e.message);
    return null;
  }
}

// -- polling -------------------------------------------------------------------

async function poll() {
  try {
    const prevBusy = state.busy;
    state = await api("/api/state");
    renderLibrary();
    renderJobs();
    if (waitingFor) {
      const job = state.jobs.find((j) => j.id === waitingFor);
      if (job && !["queued", "running"].includes(job.state)) {
        waitingFor = null;
        showResult(await api(`/api/job?id=${job.id}`));
      }
    }
    if (openLog) renderLog(await api(`/api/job?id=${openLog}`).catch(() => null));
    if (prevBusy && !state.busy) refreshAlbumPanel();
  } catch (e) {
    console.warn("poll failed", e);
  }
  schedulePoll(state.busy || waitingFor ? 1500 : 8000);
}

function schedulePoll(ms) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(poll, ms);
}

// -- library ---------------------------------------------------------------------

function renderLibrary() {
  $("#libpath").textContent = state.library || "";
  const grid = $("#grid");
  grid.replaceChildren(...state.albums.map(card));
  $("#empty").hidden = state.albums.length > 0;
}

function card(a) {
  const cover = a.cover
    ? h("img", { class: "cover", src: `/api/cover?id=${encodeURIComponent(a.id)}&t=${a.done}`, alt: "", loading: "lazy" })
    : h("div", { class: "cover none" }, "♪");
  const status = a.failed ? h("span", { class: "badge bad" }, `${a.failed} failed`)
    : a.done < a.tracks ? h("span", { class: "badge" }, `${a.done}/${a.tracks}`) : h("span", { class: "badge ok" }, `${a.tracks} tracks`);
  return h("button", { class: "card", type: "button", onclick: () => openAlbum(a.id), title: `${a.albumartist} — ${a.album}` },
    cover,
    h("div", { class: "meta" },
      h("div", { class: "title" }, a.album),
      h("div", { class: "artist" }, a.albumartist),
      h("div", { class: "info" }, a.year ? `${a.year} ` : "", status, a.mb ? h("span", { class: "badge mb" }, "MB") : null)));
}

// -- one album: view and edit ---------------------------------------------------------

let currentAlbum = null;

async function openAlbum(id) {
  try {
    currentAlbum = await api(`/api/album?id=${encodeURIComponent(id)}`);
  } catch (e) {
    return alert(e.message);
  }
  renderAlbum();
  $("#album").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function refreshAlbumPanel() {
  if (!currentAlbum) return;
  try {
    currentAlbum = await api(`/api/album?id=${encodeURIComponent(currentAlbum.source_id)}`);
    renderAlbum();
  } catch { /* album moved or gone */ }
}

function provBadge(p) {
  return p ? h("span", { class: `badge ${p === "mb" ? "mb" : p === "user" ? "user" : ""}` }, PROV[p] || p) : null;
}

function renderAlbum() {
  const p = currentAlbum;
  const panel = $("#album");
  const field = (label, name, value, type = "text") =>
    h("label", {}, h("span", {}, label, " ", provBadge(p.provenance[name])), h("input", { type, name, value: value ?? "" }));
  const rows = p.tracks.map((t) =>
    h("tr", { "data-id": t.video_id },
      h("td", { class: "num" }, t.disc > 1 ? `${t.disc}-${t.number}` : t.number),
      h("td", {}, h("input", { type: "text", name: "artist", value: t.artist, "aria-label": "artist" })),
      h("td", {}, h("input", { type: "text", name: "title", value: t.title, "aria-label": "title" })),
      h("td", { class: "src" }, provBadge(t.provenance.title)),
      h("td", { class: "src" },
        t.state === "done" ? h("span", { class: "badge ok" }, "✓") : t.state === "failed" ? h("span", { class: "badge bad", title: t.error || "" }, "failed") : h("span", { class: "badge" }, "pending"),
        t.in_source ? null : h("span", { class: "badge", title: "no longer in the source playlist" }, "gone"))));
  const skipped = (p.skipped || []).map((s) => h("li", { class: "muted" }, `${s.title} — ${s.reason}`));
  panel.replaceChildren(
    h("div", { class: "panel-head" },
      h("div", {}, h("h2", {}, `${p.albumartist} — ${p.album}`), h("div", { class: "muted" }, `${p.kind.replace("_", " ")} · ${p.folder}`)),
      h("button", { class: "quiet", type: "button", onclick: () => { panel.hidden = true; currentAlbum = null; } }, "Close")),
    h("form", { id: "albumform", onsubmit: saveAlbum },
      h("div", { class: "fields" }, field("Album artist", "albumartist", p.albumartist), field("Album", "album", p.album), field("Year", "year", p.year, "number")),
      h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "#"), h("th", {}, "Artist"), h("th", {}, "Title"), h("th", {}, "from"), h("th", {}, ""))), h("tbody", {}, rows)),
      skipped.length ? h("details", {}, h("summary", { class: "muted" }, `${skipped.length} skipped`), h("ul", {}, skipped)) : null,
      h("div", { class: "actions" },
        h("button", { type: "submit" }, "Save changes (rename + retag)"),
        h("button", { class: "quiet", type: "button", onclick: () => submit("fetch", { urls: [p.source_url] }) }, "Re-check source"),
        h("a", { href: p.source_url, target: "_blank", rel: "noopener" }, "open on YouTube"))));
  panel.hidden = false;
}

function saveAlbum(ev) {
  ev.preventDefault();
  const form = ev.target;
  const edits = {
    album: form.album.value, albumartist: form.albumartist.value, year: form.year.value,
    tracks: [...form.querySelectorAll("tbody tr")].map((tr) => ({
      video_id: tr.dataset.id, artist: tr.querySelector("[name=artist]").value, title: tr.querySelector("[name=title]").value,
    })),
  };
  submit("edit", { id: currentAlbum.source_id, edits });
}

// -- adding: preview / search / channel results -------------------------------------------

function showResult(job) {
  const panel = $("#results");
  const close = h("button", { class: "quiet", type: "button", onclick: () => { panel.hidden = true; } }, "Close");
  const r = job.result;
  if (job.state !== "done" || !r) {
    panel.replaceChildren(h("div", { class: "panel-head" }, h("h2", {}, "That did not work"), close),
      h("pre", {}, (job.log || []).slice(-8).join("\n")));
  } else if (job.kind === "preview") {
    panel.replaceChildren(...previewView(r.plan, close));
  } else {
    panel.replaceChildren(...pickView(r, close));
  }
  panel.hidden = false;
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function previewView(p, close) {
  const rows = p.tracks.map((t) => h("tr", {}, h("td", { class: "num" }, t.number), h("td", {}, t.artist), h("td", {}, t.title),
    h("td", { class: "src" }, provBadge(t.provenance.artist), provBadge(t.provenance.title))));
  return [
    h("div", { class: "panel-head" },
      h("div", {}, h("h2", {}, `${p.albumartist} — ${p.album}`, p.year ? ` (${p.year})` : ""), h("div", { class: "muted" }, `${p.kind.replace("_", " ")} · ${p.tracks.length} tracks → ${p.folder}`)),
      close),
    h("table", {}, h("tbody", {}, rows)),
    p.skipped?.length ? h("p", { class: "muted" }, `skipped: ${p.skipped.map((s) => `${s.title} (${s.reason})`).join("; ")}`) : null,
    h("div", { class: "actions" }, h("button", { type: "button", onclick: () => { submit("fetch", { urls: [p.source_url] }); $("#results").hidden = true; } }, "Download")),
  ];
}

function pickView(r, close) {
  const have = new Set(state.albums.map((a) => a.id));
  const groups = (r.groups || []).map((g) =>
    h("div", { class: "group" }, h("h3", {}, g.label),
      g.refs.map((ref) => h("label", { class: "pick" },
        h("input", { type: "checkbox", value: ref.url }),
        h("span", {}, ref.title),
        ref.tab === "search" && ref.artist ? h("span", { class: "muted" }, `by ${ref.artist}`) : null,
        ref.count ? h("span", { class: "badge" }, `${ref.count} tracks`) : null,
        have.has(ref.id) ? h("span", { class: "badge ok" }, "in library") : null))));
  const download = () => {
    const urls = [...$("#results").querySelectorAll("input[type=checkbox]:checked")].map((c) => c.value);
    if (urls.length) { submit("fetch", { urls }); $("#results").hidden = true; }
  };
  return [
    h("div", { class: "panel-head" }, h("h2", {}, r.groups?.length ? "Found" : "Nothing found"), close),
    r.missing?.length ? h("p", { class: "muted" }, `MusicBrainz lists studio albums not found on YouTube: ${r.missing.join("; ")}`) : null,
    ...groups,
    r.groups?.length ? h("div", { class: "actions" }, h("button", { type: "button", onclick: download }, "Download selected")) : null,
  ];
}

// -- jobs ------------------------------------------------------------------------------

function renderJobs() {
  const recent = state.jobs.filter((j) => ["queued", "running"].includes(j.state) || Date.now() / 1000 - (j.finished || 0) < 120).slice(0, 4);
  $("#jobs").replaceChildren(...recent.map((j) =>
    h("div", { class: `job ${j.state}`, "data-id": j.id },
      h("div", {}, h("strong", {}, j.label), " ", h("span", { class: "badge" }, j.state), " ",
        h("button", { class: "quiet", type: "button", onclick: () => { openLog = openLog === j.id ? null : j.id; poll(); } }, openLog === j.id ? "hide log" : "log")),
      openLog === j.id ? h("pre", { id: `log-${j.id}` }) : h("div", { class: "line" }, (j.log || []).at(-1) || ""))));
}

function renderLog(job) {
  const pre = job && document.getElementById(`log-${job.id}`);
  if (pre) { pre.textContent = job.log.join("\n"); pre.scrollTop = pre.scrollHeight; }
}

// -- wiring ------------------------------------------------------------------------------

$("#open").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const q = $("#q").value.trim();
  if (!q) return;
  const id = await submit("open", { q });
  if (id) {
    waitingFor = id;
    $("#results").replaceChildren(h("p", { class: "muted" }, /^https?:/.test(q) ? "Reading from YouTube…" : `Searching for “${q}”…`));
    $("#results").hidden = false;
  }
});
$("#update").addEventListener("click", () => submit("update", {}));

if ("serviceWorker" in navigator && window.isSecureContext) navigator.serviceWorker.register("/sw.js").catch(() => {});
poll();
