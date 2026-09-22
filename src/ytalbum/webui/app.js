// ytalbum web UI. No framework, no build step. All server text goes in via textContent.
"use strict";

const $ = (sel) => document.querySelector(sel);
const PROV = { mb: "MB", yt_music: "YT Music", yt_title: "title", playlist: "playlist", user: "you" };
let state = { albums: [], jobs: [], busy: false };
let waitingFor = null; // job id whose result the results panel is waiting for
let openLog = null; // job id whose full log is expanded
let pollTimer = null;

// DOM's replaceChildren turns null into the text "null"; drop empty children first
const kids = (...children) => children.flat(Infinity).filter((c) => c != null && c !== false && c !== "");
const fill = (el, ...children) => el.replaceChildren(...kids(...children));

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

const mine = new Map(); // job id -> { button, label } for jobs started in this page

async function submit(action, body, button = null) {
  if (button) setWorking(button, true);
  try {
    const { job } = await api(`/api/${action}`, body);
    mine.set(job.id, { button, label: job.label });
    state.jobs.unshift(job);
    state.busy = true;
    renderJobs();
    renderActivity();
    schedulePoll(300);
    return job.id;
  } catch (e) {
    if (button) setWorking(button, false);
    toast(e.message, "failed");
    return null;
  }
}

function setWorking(button, on) {
  if (on) {
    button.dataset.label = button.dataset.label || button.textContent;
    button.textContent = "Working…";
  } else if (button.dataset.label) {
    button.textContent = button.dataset.label;
  }
  button.classList.toggle("working", on);
  button.disabled = on;
}

// when a job of ours finishes: free its button, say how it went
function settleJobs() {
  for (const [id, info] of mine) {
    const job = state.jobs.find((j) => j.id === id);
    if (!job || ["queued", "running"].includes(job.state)) continue;
    mine.delete(id);
    if (info.button?.isConnected) setWorking(info.button, false);
    const last = (job.log || []).filter((l) => !l.startsWith("  ")).at(-1) || "";
    const text = { done: "✓", failed: "✗", blocked: "⏸", cancelled: "⏹" }[job.state] + ` ${info.label}` + (last ? ` — ${last}` : "");
    toast(text, job.state);
  }
}

function toast(text, kind = "done") {
  const el = h("div", { class: `toast ${kind === "cancelled" ? "blocked" : kind}`, role: "status" }, text);
  $("#toasts").append(el);
  setTimeout(() => el.classList.add("gone"), kind === "done" ? 5000 : 9000);
  setTimeout(() => el.remove(), kind === "done" ? 5600 : 9600);
}

// the header says what is going on right now
function renderActivity() {
  const running = state.jobs.find((j) => j.state === "running") || state.jobs.find((j) => j.state === "queued");
  const el = $("#activity");
  el.hidden = !state.busy || !running;
  if (!el.hidden) {
    const queued = state.jobs.filter((j) => j.state === "queued").length;
    fill(el, h("span", { class: "dot" }), h("strong", {}, running.label),
      h("span", { class: "muted" }, " ", (running.log || []).at(-1) || "starting…"),
      queued > (running.state === "queued" ? 1 : 0) ? h("span", { class: "badge" }, `+${queued - (running.state === "queued" ? 1 : 0)} queued`) : null,
      cancelButton(running));
  }
}

// -- polling -------------------------------------------------------------------

async function poll() {
  try {
    const prevBusy = state.busy;
    state = await api("/api/state");
    renderLibrary();
    renderJobs();
    renderSettings();
    renderActivity();
    settleJobs();
    if (waitingFor) {
      const job = state.jobs.find((j) => j.id === waitingFor);
      if (job && !["queued", "running"].includes(job.state)) {
        waitingFor = null;
        showResult(await api(`/api/job?id=${job.id}`));
      }
    }
    if (openLog) renderLog(await api(`/api/job?id=${openLog}`).catch(() => null));
    if (prevBusy && !state.busy) refreshAlbumPanel();
    setOffline(false);
  } catch (e) {
    console.warn("poll failed", e);
    setOffline(true);
  }
  schedulePoll(offline ? 3000 : state.busy || waitingFor ? 700 : 8000);
}

let offline = false;

function setOffline(on) {
  if (on === offline) return;
  offline = on;
  $("#offline").hidden = !on;
}

function schedulePoll(ms) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(poll, ms);
}

// -- library ---------------------------------------------------------------------

function renderLibrary() {
  $("#libpath").textContent = state.library || "";
  const grid = $("#grid");
  fill(grid, state.albums.map(card));
  $("#empty").hidden = state.albums.length > 0;
}

function card(a) {
  const cover = a.cover
    ? h("img", { class: "cover", src: `/api/cover?id=${encodeURIComponent(a.id)}&t=${a.done}`, alt: "", loading: "lazy" })
    : h("div", { class: "cover none" }, "♪");
  const status = a.failed ? h("span", { class: "badge bad" }, `${a.failed} failed`)
    : a.done < a.tracks ? h("span", { class: "badge" }, `${a.done}/${a.tracks}`) : h("span", { class: "badge ok" }, `${a.tracks} tracks`);
  const play = a.done ? h("span", { class: "card-play", role: "button", tabindex: "0", title: "Play album", "aria-label": `Play ${a.album}`,
    onclick: (e) => { e.stopPropagation(); playAlbum(a.id, 0); },
    onkeydown: (e) => { if (e.key === "Enter") { e.stopPropagation(); e.preventDefault(); playAlbum(a.id, 0); } } }, "▶") : null;
  return h("button", { class: "card", type: "button", onclick: () => openAlbum(a.id), title: `${a.albumartist} — ${a.album}` },
    h("div", { class: "cover-wrap" }, cover, play),
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

const rowKey = (t) => [t.number, t.artist, t.title, t.state, t.in_source].join("|");

async function refreshAlbumPanel() {
  if (!currentAlbum) return;
  const before = new Map(currentAlbum.tracks.map((t) => [t.video_id, rowKey(t)]));
  try {
    currentAlbum = await api(`/api/album?id=${encodeURIComponent(currentAlbum.source_id)}`);
  } catch { return; /* album moved or gone */ }
  renderAlbum();
  for (const tr of document.querySelectorAll("#album tbody tr")) {
    const t = currentAlbum.tracks.find((x) => x.video_id === tr.dataset.id);
    if (t && before.get(t.video_id) !== rowKey(t)) tr.classList.add("changed");
  }
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
    h("tr", { "data-id": t.video_id, class: isPlaying(p.source_id, t.video_id) ? "playing" : "" },
      h("td", { class: "num" },
        t.state === "done" ? h("button", { class: "row-play", type: "button", title: "Play from here", "aria-label": `Play ${t.title}`,
          onclick: () => playAlbum(p.source_id, p.tracks.filter((x) => x.state === "done").findIndex((x) => x.video_id === t.video_id)) }, "▶") : null,
        h("span", { class: "n" }, t.disc > 1 ? `${t.disc}-${t.number}` : t.number)),
      h("td", {}, h("input", { type: "text", name: "artist", value: t.artist, "aria-label": "artist" })),
      h("td", {}, h("input", { type: "text", name: "title", value: t.title, "aria-label": "title" })),
      h("td", { class: "src" }, provBadge(t.provenance.title)),
      h("td", { class: "src" },
        t.state === "done" ? h("span", { class: "badge ok" }, "✓") : t.state === "failed" ? h("span", { class: "badge bad", title: t.error || "" }, "failed") : h("span", { class: "badge" }, "pending"),
        t.in_source ? null : h("span", { class: "badge", title: "no longer in the source playlist" }, "gone"))));
  const skipped = (p.skipped || []).map((s) => h("li", { class: "muted" }, `${s.title} — ${s.reason}`));
  const gone = p.tracks.filter((t) => !t.in_source);
  fill(panel,
    h("div", { class: "panel-head" },
      h("div", {}, h("h2", {}, `${p.albumartist} — ${p.album}`), h("div", { class: "muted" }, `${p.kind.replace("_", " ")} · ${p.folder}`)),
      h("button", { class: "quiet", type: "button", onclick: () => { panel.hidden = true; currentAlbum = null; } }, "Close")),
    h("form", { id: "albumform", onsubmit: saveAlbum },
      h("div", { class: "fields" }, field("Album artist", "albumartist", p.albumartist), field("Album", "album", p.album), field("Year", "year", p.year, "number")),
      h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "#"), h("th", {}, "Artist"), h("th", {}, "Title"), h("th", {}, "from"), h("th", {}, ""))), h("tbody", {}, rows)),
      skipped.length ? h("details", {}, h("summary", { class: "muted" }, `${skipped.length} skipped`), h("ul", {}, skipped)) : null,
      h("div", { class: "actions" },
        h("button", { type: "submit" }, "Save changes (rename + retag)"),
        h("button", { class: "quiet", type: "button", onclick: (e) => submit("fetch", { urls: [p.source_url] }, e.currentTarget) }, "Re-check source"),
        gone.length ? h("button", { class: "danger", type: "button", onclick: (e) => pruneAlbum(p, gone, e.currentTarget) }, `Remove ${gone.length} track${gone.length > 1 ? "s" : ""} no longer in the playlist`) : null,
        h("a", { href: p.source_url, target: "_blank", rel: "noopener" }, "open on YouTube"))));
  panel.hidden = false;
}

function pruneAlbum(p, gone, button) {
  const list = gone.map((t) => `  ${t.number}. ${t.artist} – ${t.title}`).join("\n");
  if (confirm(`Delete these files? They are no longer in the YouTube playlist:\n\n${list}`)) submit("prune", { id: p.source_id }, button);
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
  submit("edit", { id: currentAlbum.source_id, edits }, ev.submitter);
}

// -- adding: preview / search / channel results -------------------------------------------

function showResult(job) {
  const panel = $("#results");
  const close = h("button", { class: "quiet", type: "button", onclick: () => { panel.hidden = true; clearTimeout(detailTimer); } }, "Close");
  const r = job.result;
  if (job.state !== "done" || !r) {
    fill(panel, h("div", { class: "panel-head" }, h("h2", {}, "That did not work"), close),
      h("pre", {}, (job.log || []).slice(-8).join("\n")));
  } else if (job.kind === "preview") {
    fill(panel, previewView(r.plan, close));
  } else {
    fill(panel, pickView(r, close));
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
    h("div", { class: "actions" }, h("button", { type: "button", onclick: (e) => { submit("fetch", { urls: [p.source_url] }, e.currentTarget).then(() => setTimeout(() => { $("#results").hidden = true; }, 1200)); } }, "Download")),
  ];
}

function pickView(r, close) {
  const have = new Set(state.albums.map((a) => a.id));
  const groups = (r.groups || []).map((g) => {
    const cards = g.refs.map((ref) => pickCard(ref, have.has(ref.id)));
    const all = h("button", { class: "quiet small", type: "button", onclick: () => {
      const boxes = cards.map((c) => c.querySelector("input"));
      const turnOn = boxes.some((b) => !b.checked);
      boxes.forEach((b) => { b.checked = turnOn; b.closest(".pick").classList.toggle("on", turnOn); });
      updatePickCount();
    } }, "select all");
    return h("div", { class: "group" }, h("h3", {}, g.label, h("span", { class: "badge" }, g.refs.length), all), h("div", { class: "picks" }, cards));
  });
  fillDetails((r.groups || []).flatMap((g) => g.refs));
  return [
    h("div", { class: "panel-head" },
      h("div", {}, h("h2", {}, r.groups?.length ? "Found" : "Nothing found"),
        r.channel ? h("a", { class: "muted", href: r.channel, target: "_blank", rel: "noopener" }, "artist channel on YouTube") : null),
      close),
    r.missing?.length ? h("p", { class: "muted missing" }, `Not found on YouTube: ${r.missing.join(" · ")}`) : null,
    ...groups,
    r.groups?.length ? h("div", { class: "actions sticky" },
      h("button", { type: "button", id: "pick-download", disabled: true, onclick: downloadPicked }, "Download selected"),
      h("span", { id: "pick-count", class: "muted" }, "nothing selected")) : null,
  ];
}

function pickCard(ref, inLibrary) {
  const box = h("input", { type: "checkbox", value: ref.url, onclick: (e) => e.stopPropagation() });
  const label = h("label", { class: `pick${inLibrary ? " have" : ""}`, "data-id": ref.id, onclick: () => setTimeout(updatePickCount) },
    h("div", { class: "pick-cover" }, cover(ref), box),
    h("div", { class: "pick-title" }, ref.title),
    h("div", { class: "muted pick-meta" }, pickMeta(ref), inLibrary ? h("span", { class: "badge ok" }, "in library") : null));
  box.addEventListener("change", () => label.classList.toggle("on", box.checked));
  return label;
}

const cover = (ref) => (ref.thumbnail
  ? h("img", { src: `/api/thumb?u=${encodeURIComponent(ref.thumbnail)}`, alt: "", loading: "lazy" })
  : h("div", { class: "cover none" }, "♪"));

const pickMeta = (ref) => h("span", { class: "meta-text" },
  [ref.count ? `${ref.count} tracks` : ref.unknown ? "" : "…", ref.tab === "search" && ref.artist ? `by ${ref.artist}` : null].filter(Boolean).join(" · ") || "\u00a0");

// details (track count, cover) are not in YouTube's listings: a background runner on the
// server fetches them one playlist at a time, we poll and fill them in as they arrive
let detailTimer = null;

async function fillDetails(refs) {
  clearTimeout(detailTimer);
  const panel = $("#results");
  const todo = () => refs.filter((r) => !r.count && !r.unknown);
  const step = async () => {
    if (panel.hidden || !todo().length) return;
    try {
      const known = await api("/api/details", { refs: todo().map((r) => ({ id: r.id, url: r.url })) });
      for (const ref of refs) {
        const info = known[ref.id];
        const card = panel.querySelector(`.pick[data-id="${CSS.escape(ref.id)}"]`);
        if (!info || !card) continue;
        Object.assign(ref, info);
        card.querySelector(".meta-text").replaceWith(pickMeta(ref));
        if (ref.thumbnail && !card.querySelector("img")) card.querySelector(".cover.none").replaceWith(cover(ref));
      }
    } catch (e) {
      console.warn("details failed", e);
    }
    detailTimer = setTimeout(step, 2000);
  };
  step();
}

function updatePickCount() {
  const n = $("#results").querySelectorAll("input[type=checkbox]:checked").length;
  const button = $("#pick-download");
  if (!button) return;
  button.disabled = n === 0;
  $("#pick-count").textContent = n ? `${n} selected` : "nothing selected";
}

function downloadPicked(e) {
  const urls = [...$("#results").querySelectorAll("input[type=checkbox]:checked")].map((c) => c.value);
  if (!urls.length) return;
  submit("fetch", { urls }, e.currentTarget).then(() => setTimeout(() => { $("#results").hidden = true; }, 1200));
}

// -- jobs ------------------------------------------------------------------------------

function cancelButton(job) {
  if (!["queued", "running"].includes(job.state)) return null;
  const asked = (job.log || []).at(-1) === "cancel requested…";
  return h("button", { class: "quiet cancel", type: "button", disabled: asked, title: "Stop after the current step; finished tracks are kept",
    onclick: async (e) => {
      e.stopPropagation();
      setWorking(e.currentTarget, true);
      try { await api("/api/cancel", { id: job.id }); schedulePoll(200); } catch (err) { toast(err.message, "failed"); }
    } }, asked ? "Stopping…" : "Cancel");
}

function renderJobs() {
  const recent = state.jobs.filter((j) => ["queued", "running"].includes(j.state) || Date.now() / 1000 - (j.finished || 0) < 120).slice(0, 4);
  fill($("#jobs"), recent.map((j) =>
    h("div", { class: `job ${j.state}`, "data-id": j.id },
      h("div", {}, h("strong", {}, j.label), " ", h("span", { class: "badge" }, j.state), " ", cancelButton(j), " ",
        h("button", { class: "quiet", type: "button", onclick: () => { openLog = openLog === j.id ? null : j.id; poll(); } }, openLog === j.id ? "hide log" : "log")),
      openLog === j.id ? h("pre", { id: `log-${j.id}` }) : h("div", { class: "line" }, (j.log || []).at(-1) || ""))));
}

function renderLog(job) {
  const pre = job && document.getElementById(`log-${job.id}`);
  if (pre) { pre.textContent = job.log.join("\n"); pre.scrollTop = pre.scrollHeight; }
}

// -- settings ----------------------------------------------------------------------------

const BROWSER_NAMES = { firefox: "Firefox", chrome: "Chrome", chromium: "Chromium", brave: "Brave", edge: "Edge", vivaldi: "Vivaldi", opera: "Opera" };

function renderSettings() {} // the panel is built when opened, so polling never overwrites what you type

function openSettings() {
  const panel = $("#settings");
  if (!panel.hidden) { panel.hidden = true; return; }
  const st = state.settings;
  if (!st) return;
  const browsers = ["", ...st.browsers];
  if (st.cookies_from_browser && !browsers.includes(st.cookies_from_browser)) browsers.push(st.cookies_from_browser);
  const row = (label, help, input) => h("label", { class: "setting" }, h("span", {}, h("strong", {}, label), h("small", { class: "muted" }, help)), input);
  fill(panel,
    h("div", { class: "panel-head" }, h("h2", {}, "Settings"), h("button", { class: "quiet", type: "button", onclick: () => { panel.hidden = true; } }, "Close")),
    h("form", { id: "settingsform", onsubmit: saveSettings },
      row("Library folder", "where albums are stored (created if missing); existing albums are not moved",
        h("input", { type: "text", name: "library", value: st.library })),
      row("YouTube login", "browser whose YouTube session is used — avoids the bot check, needed for age-restricted videos",
        h("select", { name: "cookies_from_browser" }, browsers.map((b) => h("option", { value: b, selected: b === (st.cookies_from_browser || "") }, b ? BROWSER_NAMES[b.split(":")[0]] || b : "none")))),
      row("MusicBrainz", "look up correct names, years, covers and tracklists",
        h("input", { type: "checkbox", name: "musicbrainz", checked: st.musicbrainz })),
      row("Token helper", "proof-of-origin tokens for streams YouTube withholds; server = started on demand",
        h("select", { name: "pot_mode" }, ["server", "script", "off"].map((m) => h("option", { value: m, selected: m === st.pot_mode }, m)))),
      row("Token server stops after", "minutes without YouTube activity",
        h("input", { type: "number", name: "pot_idle_minutes", min: 1, max: 120, value: st.pot_idle_minutes })),
      row("Parallel YouTube requests", "1–4; more is faster but trips YouTube's bot check sooner",
        h("input", { type: "number", name: "concurrency", min: 1, max: 4, value: st.concurrency })),
      h("dl", { class: "info" }, Object.entries(st.info).flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])),
      h("div", { class: "actions" }, h("button", { type: "submit" }, "Save settings"))));
  panel.hidden = false;
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function saveSettings(ev) {
  ev.preventDefault();
  const f = ev.target;
  const button = ev.submitter;
  setWorking(button, true);
  try {
    state.settings = await api("/api/settings", {
      library: f.library.value, cookies_from_browser: f.cookies_from_browser.value, musicbrainz: f.musicbrainz.checked,
      pot_mode: f.pot_mode.value, pot_idle_minutes: Number(f.pot_idle_minutes.value), concurrency: Number(f.concurrency.value),
    });
    toast("✓ Settings saved — they apply from the next job", "done");
    $("#settings").hidden = true;
    poll();
  } catch (e) {
    toast(e.message, "failed");
  } finally {
    setWorking(button, false);
  }
}

$("#gear").addEventListener("click", openSettings);

// -- player ----------------------------------------------------------------------------

const audio = $("#audio");
let queue = []; // [{ album, video_id, title, artist }]
let qi = -1;

const fmt = (sec) => (Number.isFinite(sec) ? `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, "0")}` : "0:00");
const isPlaying = (albumId, videoId) => qi >= 0 && queue[qi].album === albumId && queue[qi].video_id === videoId;

async function playAlbum(albumId, start = 0) {
  let plan;
  try {
    plan = currentAlbum?.source_id === albumId ? currentAlbum : await api(`/api/album?id=${encodeURIComponent(albumId)}`);
  } catch (e) {
    return toast(e.message, "failed");
  }
  queue = plan.tracks.filter((t) => t.state === "done").map((t) => ({ album: albumId, video_id: t.video_id, title: t.title, artist: t.artist, albumName: plan.album }));
  if (!queue.length) return toast("Nothing downloaded yet in this album", "blocked");
  playIndex(Math.max(0, start));
}

function playIndex(i) {
  if (i < 0 || i >= queue.length) return;
  qi = i;
  const t = queue[i];
  audio.src = `/api/audio?id=${encodeURIComponent(t.album)}&v=${encodeURIComponent(t.video_id)}`;
  audio.play().catch((e) => toast(`Cannot play: ${e.message}`, "failed"));
  $("#player").hidden = false;
  document.body.classList.add("has-player");
  $("#p-cover").src = `/api/cover?id=${encodeURIComponent(t.album)}`;
  $("#p-title").textContent = t.title;
  $("#p-artist").textContent = `${t.artist} · ${t.albumName}`;
  document.querySelectorAll("#album tbody tr").forEach((tr) => tr.classList.toggle("playing", isPlaying(currentAlbum?.source_id, tr.dataset.id)));
  if ("mediaSession" in navigator) {
    navigator.mediaSession.metadata = new MediaMetadata({ title: t.title, artist: t.artist, album: t.albumName,
      artwork: [{ src: `/api/cover?id=${encodeURIComponent(t.album)}` }] });
  }
}

audio.addEventListener("play", () => { $("#p-play").textContent = "⏸"; });
audio.addEventListener("pause", () => { $("#p-play").textContent = "▶"; });
audio.addEventListener("ended", () => (qi + 1 < queue.length ? playIndex(qi + 1) : null));
audio.addEventListener("timeupdate", () => {
  $("#p-time").textContent = fmt(audio.currentTime);
  $("#p-dur").textContent = fmt(audio.duration);
  if (document.activeElement !== $("#p-pos") && audio.duration) $("#p-pos").value = Math.round((audio.currentTime / audio.duration) * 1000);
});
audio.addEventListener("error", () => { if (audio.src) toast("This track cannot be played (moved or deleted?)", "failed"); });
$("#p-pos").addEventListener("change", (e) => { if (audio.duration) audio.currentTime = (e.target.value / 1000) * audio.duration; });
$("#p-play").addEventListener("click", () => (audio.paused ? audio.play() : audio.pause()));
$("#p-prev").addEventListener("click", () => (audio.currentTime > 3 ? (audio.currentTime = 0) : playIndex(qi - 1)));
$("#p-next").addEventListener("click", () => playIndex(qi + 1));
if ("mediaSession" in navigator) {
  navigator.mediaSession.setActionHandler("previoustrack", () => playIndex(qi - 1));
  navigator.mediaSession.setActionHandler("nexttrack", () => playIndex(qi + 1));
}

// -- theme: auto (follow the system) → dark → light, remembered in this browser ---------------

const THEMES = { auto: ["◐", "automatic"], dark: ["☾", "dark"], light: ["☀", "light"] };

function applyTheme(theme) {
  if (theme === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  $("#theme").textContent = THEMES[theme][0];
  $("#theme").title = `Theme: ${THEMES[theme][1]} (click to change)`;
}

function savedTheme() {
  try { return THEMES[localStorage.getItem("ytalbum-theme")] ? localStorage.getItem("ytalbum-theme") : "auto"; } catch { return "auto"; }
}

let theme = savedTheme();
$("#theme").addEventListener("click", () => {
  const order = ["auto", "dark", "light"];
  theme = order[(order.indexOf(theme) + 1) % order.length];
  try { localStorage.setItem("ytalbum-theme", theme); } catch { /* private mode: just this session */ }
  applyTheme(theme);
});
applyTheme(theme);

// -- wiring ------------------------------------------------------------------------------

$("#open").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const q = $("#q").value.trim();
  if (!q) return;
  const id = await submit("open", { q }, ev.submitter || $("#open button"));
  if (id) {
    waitingFor = id;
    fill($("#results"), h("p", { class: "muted" }, /^https?:/.test(q) ? "Reading from YouTube…" : `Searching for “${q}”…`));
    $("#results").hidden = false;
  }
});
$("#update").addEventListener("click", (e) => submit("update", {}, e.currentTarget));

if ("serviceWorker" in navigator && window.isSecureContext) navigator.serviceWorker.register("/sw.js").catch(() => {});
poll();
