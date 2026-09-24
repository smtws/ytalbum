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
    if (state.tracks_version && state.tracks_version !== trackIndex.version) loadTracks();
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

let gridShows = "";
let artistFilter = null;
let libFilter = "";
let trackIndex = { version: null, albums: {} }; // artist+title per album, for filtering by song

async function loadTracks() {
  const wanted = state.tracks_version;
  try {
    const got = await api("/api/tracks");
    if (got.version !== trackIndex.version) {
      trackIndex = got;
      renderLibrary();
    }
  } catch {
    trackIndex = { version: wanted, albums: {} }; // do not hammer the server on a failure
  }
}

// ignore case, accents and punctuation, so "njord" finds "Dreams of Njǫrð".
// NFD handles the combining marks; these letters are separate characters and never decompose.
const LETTERS = { ð: "d", þ: "th", ø: "o", æ: "ae", œ: "oe", ß: "ss", ł: "l", đ: "d", ŋ: "n", ʒ: "z" };

// Folds one character at a time and remembers where each folded character came from, so a
// match can be pointed back at the original text ("ü" -> "ue" is two characters from one).
function foldMap(text, german) {
  let folded = "";
  const from = [];
  for (let i = 0; i < (text || "").length; i++) {
    let c = text[i].normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
    if (german && (c === "a" || c === "o" || c === "u") && text[i].normalize("NFD").length > 1) c += "e";
    c = c.replace(/[ðþøæœßłđŋʒ]/g, (x) => LETTERS[x]).replace(/[^\p{L}\p{N}]+/gu, " ");
    for (const _ of c) from.push(i);
    folded += c;
  }
  return { folded, from };
}

const fold = (s) => foldMap(s, false).folded;

// German keyboards without umlauts write "knueppel" for "Knüppel", which folding to
// "knuppel" would miss — so every string is matched (and highlighted) in both spellings.
const maps = (text) => [foldMap(text, false), foldMap(text, true)];
const hits = (terms, text) => {
  const both = maps(text);
  return terms.every((term) => both.some((m) => m.folded.includes(term)));
};

// Where each term sits in the original string, as [start, end) ranges, merged and sorted.
function matchRanges(text, terms) {
  const ranges = [];
  for (const m of maps(text)) {
    for (const term of terms) {
      for (let at = m.folded.indexOf(term); at !== -1; at = m.folded.indexOf(term, at + 1)) {
        ranges.push([m.from[at], m.from[at + term.length - 1] + 1]);
      }
    }
  }
  ranges.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const merged = [];
  for (const r of ranges) {
    const last = merged.at(-1);
    if (last && r[0] <= last[1]) last[1] = Math.max(last[1], r[1]);
    else merged.push([...r]);
  }
  return merged;
}

// The matched part of a string, wrapped in <mark>; plain text when nothing is being filtered.
function marked(text) {
  const terms = libFilter ? fold(libFilter).split(" ").filter(Boolean) : [];
  const ranges = terms.length ? matchRanges(text, terms) : [];
  if (!ranges.length) return text;
  const out = [];
  let at = 0;
  for (const [from, to] of ranges) {
    if (from > at) out.push(text.slice(at, from));
    out.push(h("mark", {}, text.slice(from, to)));
    at = to;
  }
  if (at < text.length) out.push(text.slice(at));
  return out;
}

// An album matches by its own name, or because a song in it does — "pers" finds Perséfone
// inside Vol. 3, not only "Nocturnal Whispers".
const TRACK = { id: 0, artist: 1, title: 2, done: 3, start: 4, end: 5 }; // rows from /api/tracks

function matchingRows(a, terms) {
  return (trackIndex.albums[a.id] || []).filter((r) => hits(terms, `${r[TRACK.artist]} ${r[TRACK.title]}`));
}

function matchingTracks(a, terms) {
  return matchingRows(a, terms).map((r) => `${r[TRACK.artist]} — ${r[TRACK.title]}`);
}

function shownAlbums() {
  const byArtist = artistFilter ? state.albums.filter((a) => a.albumartist === artistFilter) : state.albums;
  if (!libFilter) return byArtist.map((a) => ({ ...a, matches: null }));
  const terms = fold(libFilter).split(" ").filter(Boolean);
  const out = [];
  for (const a of byArtist) {
    const own = hits(terms, `${a.albumartist} ${a.album} ${a.year || ""}`);
    const songs = matchingTracks(a, terms);
    if (own || songs.length) out.push({ ...a, matches: own ? null : songs });
  }
  return out;
}

function showArtist(name) {
  artistFilter = name;
  $("#libtitle").textContent = name || "Library";
  $("#artist-actions").hidden = !name;
  renderLibrary();
  $("#grid").querySelector(".card")?.focus();
}

function renderLibrary() {
  const shown = shownAlbums().length;
  const all = (artistFilter ? state.albums.filter((a) => a.albumartist === artistFilter) : state.albums).length;
  const songMatches = shownAlbums().reduce((n, a) => n + (a.matches?.length || 0), 0);
  $("#libpath").textContent = libFilter
    ? `${shown} of ${all} albums` + (songMatches ? `, ${songMatches} track${songMatches > 1 ? "s" : ""}` : "")
    : artistFilter
      ? `${all} albums`
      : state.library || "";
  $("#empty").textContent = libFilter
    ? `Nothing in the library matches “${libFilter}”.`
    : "Nothing here yet. Paste a playlist URL or type an artist above.";
  const grid = $("#grid");
  const signature = JSON.stringify(shownAlbums()) + artistFilter + libFilter + trackIndex.version;
  if (signature !== gridShows) {
    // rebuilding throws away the focused card, which would break arrow-key navigation
    const focused = document.activeElement?.closest?.("#grid .card")?.dataset.id;
    gridShows = signature;
    fill(grid, shownAlbums().map(card));
    if (focused) grid.querySelector(`.card[data-id="${CSS.escape(focused)}"]`)?.focus();
  }
  $("#empty").hidden = shownAlbums().length > 0;
  renderPlayMatches();
}

function card(a) {
  const cover = a.cover
    ? h("img", { class: "cover", src: `/api/cover?id=${encodeURIComponent(a.id)}&t=${a.done}`, alt: "", loading: "lazy" })
    : h("div", { class: "cover none" }, "♪");
  const status = a.needs_choice ? h("span", { class: "badge warn" }, `${a.needs_choice} need${a.needs_choice > 1 ? "" : "s"} a choice`)
    : a.failed ? h("span", { class: "badge bad" }, `${a.failed} failed`)
    : a.done < a.tracks ? h("span", { class: "badge" }, `${a.done}/${a.tracks}`) : h("span", { class: "badge ok" }, `${a.tracks} tracks`);
  const play = a.done ? h("span", { class: "card-play", role: "button", tabindex: "0", title: "Play album", "aria-label": `Play ${a.album}`,
    onclick: (e) => { e.stopPropagation(); playAlbum(a.id, 0); },
    onkeydown: (e) => { if (e.key === "Enter") { e.stopPropagation(); e.preventDefault(); playAlbum(a.id, 0); } } }, "▶") : null;
  // when the album is only here because a song matched, name the song rather than count it
  const songs = a.matches?.length
    ? h("div", { class: "matchline", title: a.matches.slice(0, 8).join("\n") },
        "♪ ", marked(a.matches[0]), a.matches.length > 1 ? `  +${a.matches.length - 1}` : "")
    : null;
  return h("button", { class: `card${a.id === lastAlbumId ? " current" : ""}`, type: "button", "data-id": a.id,
      onclick: () => openAlbum(a.id),
      title: a.matches?.length ? `${a.albumartist} — ${a.album}\n${a.matches.slice(0, 8).join("\n")}` : `${a.albumartist} — ${a.album}`,
      "aria-keyshortcuts": "Enter P" },
    h("div", { class: "cover-wrap" }, cover, play),
    h("div", { class: "meta" },
      h("div", { class: "title" }, marked(a.album)),
      h("div", { class: "artist link", role: "button", tabindex: "-1", title: `Show only ${a.albumartist}`,
        onclick: (e) => { e.stopPropagation(); showArtist(a.albumartist); } }, marked(a.albumartist)),
      h("div", { class: "info" }, a.year ? `${a.year} ` : "", status, a.mb ? h("span", { class: "badge mb" }, "MB") : null),
      songs));
}

// Play what the filter found: the matching songs of each album, or all of an album that
// matched by name — the same thing the cards show.
function playMatches() {
  const terms = fold(libFilter).split(" ").filter(Boolean);
  const wanted = [];
  for (const a of shownAlbums()) {
    const rows = a.matches ? matchingRows(a, terms) : trackIndex.albums[a.id] || [];
    for (const r of rows) {
      if (!r[TRACK.done]) continue;
      wanted.push({ album: a.id, video_id: r[TRACK.id], title: r[TRACK.title], artist: r[TRACK.artist],
        albumName: a.album, start: r[TRACK.start], end: r[TRACK.end] });
    }
  }
  if (!wanted.length) return toast("Nothing downloaded among the matches", "blocked");
  queue = wanted;
  playIndex(0);
}

function renderPlayMatches() {
  const n = libFilter ? shownAlbums().reduce((sum, a) => sum + (a.matches?.length ?? (trackIndex.albums[a.id] || []).filter((r) => r[TRACK.done]).length), 0) : 0;
  const button = $("#play-matches");
  button.hidden = !n;
  button.textContent = `▶ Play ${n} track${n > 1 ? "s" : ""}`;
}

$("#play-matches").addEventListener("click", playMatches);

$("#libfilter").addEventListener("input", (e) => {
  libFilter = e.target.value.trim();
  renderLibrary();
  if (!$("#album").hidden) markAlbumFields();
});
// Enter jumps into the results; Escape clears the filter before anything else closes
$("#libfilter").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("#grid").querySelector(".card")?.focus();
  else if (e.key === "Escape" && libFilter) {
    e.stopPropagation();
    e.target.value = libFilter = "";
    renderLibrary();
    if (!$("#album").hidden) markAlbumFields();
  }
});

// grid: arrows move, Enter opens, P plays
$("#grid").addEventListener("keydown", (e) => {
  const card = e.target.closest(".card");
  if (!card) return;
  const cards = [...document.querySelectorAll("#grid .card")];
  const index = cards.indexOf(card);
  const perRow = Math.max(1, cards.filter((c) => c.offsetTop === cards[0].offsetTop).length);
  const step = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: perRow, ArrowUp: -perRow, Home: -index, End: cards.length - 1 - index }[e.key];
  if (step !== undefined) {
    e.preventDefault();
    cards[Math.min(Math.max(index + step, 0), cards.length - 1)].focus();
  } else if (e.key.toLowerCase() === "p") {
    e.preventDefault();
    playAlbum(card.dataset.id, 0);
  }
});

// -- one album: view and edit ---------------------------------------------------------

let currentAlbum = null;
let lastAlbumId = (() => { try { return localStorage.getItem("ytalbum-last"); } catch { return null; } })();

// closing the editor hands focus back to the album's tile, so the keyboard keeps working
function closeAlbum(focusId = currentAlbum?.source_id) {
  $("#album").hidden = true;
  currentAlbum = null;
  const grid = $("#grid");
  const tile = focusId && grid.querySelector(`.card[data-id="${CSS.escape(focusId)}"]`);
  (tile || grid.querySelector(".card"))?.focus();
}

function markAlbum(id) {
  lastAlbumId = id;
  try { localStorage.setItem("ytalbum-last", id); } catch { /* private mode */ }
  for (const card of document.querySelectorAll("#grid .card")) card.classList.toggle("current", card.dataset.id === id);
}

let showDiscs = null; // the disc column: null = automatic (shown when the album has discs)

async function openAlbum(id) {
  markAlbum(id);
  showDiscs = null;  // back to automatic for the album being opened
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
  const discs = showDiscs ?? Math.max(1, ...p.tracks.map((t) => t.disc)) > 1;
  const panel = $("#album");
  const field = (label, name, value, type = "text") =>
    h("label", {}, h("span", {}, label, " ", provBadge(p.provenance[name])), h("input", { type, name, value: value ?? "" }));
  const rows = p.tracks.map((t) =>
    h("tr", { "data-id": t.video_id, class: isPlaying(p.source_id, t.video_id) ? "playing" : "" },
      h("td", { class: "num" },
        t.state === "done" ? h("button", { class: "row-play", type: "button", title: "Play from here", "aria-label": `Play ${t.title}`,
          onclick: () => playAlbum(p.source_id, p.tracks.filter((x) => x.state === "done").findIndex((x) => x.video_id === t.video_id)) }, "▶") : null,
        h("span", { class: "n" }, t.number)),
      h("td", {}, h("input", { type: "text", name: "artist", value: t.artist, "aria-label": "artist" })),
      h("td", {}, h("input", { type: "text", name: "title", value: t.title, "aria-label": "title" })),
      discs
        ? h("td", { class: "disc" },
            h("input", { type: "number", name: "disc", class: "disc", min: "1", step: "1", value: t.disc,
              "aria-label": `disc of ${t.title}`, title: "Which disc this track belongs to" }))
        : null,
      h("td", { class: "trim" },
        h("input", { type: "text", name: "trim_start", value: asTime(t.trim_start), placeholder: "0:00", "aria-label": "cut from the front", size: 5,
          oninput: (e) => suggestEnd(e.currentTarget, t) }),
        h("input", { type: "text", name: "trim_end", value: asTime(t.trim_end), placeholder: "end", "aria-label": "play until", size: 5 }),
        t.mb_length ? h("span", { class: `muted mb-len${usableLength(t) ? "" : " unusable"}`,
          title: usableLength(t)
            ? `MusicBrainz: the song is ${asTime(t.mb_length)} long — used to suggest the end`
            : `MusicBrainz knows a ${asTime(t.mb_length)} version, but this file is ${asTime(t.duration)} — no suggestion` },
          asTime(t.mb_length)) : null,
        t.channel ? h("button", { class: "quiet small", type: "button", title: `Apply this trim to every track from ${t.channel} in the library`,
          onclick: (e) => trimChannel(t, e.currentTarget) }, "⇉") : null),
      h("td", { class: "src" }, provBadge(t.provenance.title)),
      h("td", { class: "src" },
        t.state === "done" ? h("span", { class: "badge ok" }, "✓")
          : t.error_kind === "no_audio_stream" ? h("button", { class: "quiet small", type: "button", title: t.error || "", onclick: (e) => askAudioChoice(p, t, e.currentTarget) }, "no audio — choose")
          : t.state === "failed" ? h("span", { class: "badge bad", title: t.error || "" }, "failed") : h("span", { class: "badge" }, "pending"),
        t.ext === "m4a" ? h("span", { class: "badge", title: "audio taken from the video stream (copied, not re-encoded)" }, "m4a") : null,
        t.in_source ? null : h("span", { class: "badge", title: "no longer in the source playlist" }, "gone"),
        h("button", { class: "quiet small danger-text", type: "button", title: "Delete this track (file is removed)",
          onclick: (e) => deleteTrack(p, t, e.currentTarget) }, "✕"))));
  const skipped = (p.skipped || []).map((s) => h("li", { class: "muted" }, `${s.title} — ${s.reason}`));
  const gone = p.tracks.filter((t) => !t.in_source);
  fill(panel,
    h("div", { class: "panel-head" },
      h("div", { class: "album-head" },
        h("img", { class: "album-cover", src: `/api/cover?id=${encodeURIComponent(p.source_id)}&t=${p.tracks.filter((t) => t.state === "done").length}`,
          alt: "", title: "Play album", onclick: () => playAlbum(p.source_id, 0), onerror: (e) => { e.currentTarget.hidden = true; } }),
        h("div", {}, h("h2", {},
          h("span", { class: "link", role: "button", tabindex: "0", title: `Show all albums by ${p.albumartist}`,
            onclick: () => { const name = p.albumartist; closeAlbum(); showArtist(name); },
            onkeydown: (e) => { if (e.key === "Enter") { const name = p.albumartist; closeAlbum(); showArtist(name); } } }, p.albumartist),
          ` — ${p.album}`, p.year ? h("span", { class: "muted" }, ` (${p.year})`) : null),
          h("div", { class: "muted" }, `${p.kind.replace("_", " ")} · ${p.tracks.length} tracks · ${p.folder}`))),
      h("div", { class: "head-actions" },
        h("button", { class: "quiet", type: "button", title: "Put tracks on separate discs (each disc is numbered from 1 again)",
          onclick: () => { showDiscs = !discs; renderAlbum(); } }, discs ? "Hide discs" : "Discs…"),
        h("button", { class: "quiet", type: "button", onclick: () => closeAlbum() }, "Close"))),
    h("form", { id: "albumform", onsubmit: saveAlbum },
      h("div", { class: "fields" }, field("Album artist", "albumartist", p.albumartist), field("Album", "album", p.album), field("Year", "year", p.year, "number")),
      h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "#"), h("th", {}, "Artist"), h("th", {}, "Title"), discs ? h("th", { title: "each disc is numbered from 1" }, "disc") : null, h("th", { title: "cut the front / play until — for label idents and previews" }, "trim"), h("th", {}, "from"), h("th", {}, ""))), h("tbody", {}, rows)),
      skipped.length ? h("details", {}, h("summary", { class: "muted" }, `${skipped.length} skipped`), h("ul", {}, skipped)) : null,
      h("div", { class: "actions" },
        h("button", { type: "submit" }, "Save changes (rename + retag + trim)"),
        h("button", { class: "quiet", type: "button", onclick: (e) => submit("fetch", { urls: [p.source_url] }, e.currentTarget) }, "Re-check source"),
        h("button", { class: "danger", type: "button", onclick: (e) => deleteAlbum(p, e.currentTarget) }, "Delete album"),
        gone.length ? h("button", { class: "danger", type: "button", onclick: (e) => pruneAlbum(p, gone, e.currentTarget) }, `Remove ${gone.length} track${gone.length > 1 ? "s" : ""} no longer in the playlist`) : null,
        h("a", { href: p.source_url, target: "_blank", rel: "noopener" }, "open on YouTube"))));
  panel.hidden = false;
  markAlbumFields();
}

// The value of an input cannot be highlighted character by character — the whole field is
// tinted instead, so an album opened from a filtered library shows which fields matched.
function markAlbumFields() {
  const terms = libFilter ? fold(libFilter).split(" ").filter(Boolean) : [];
  for (const el of $("#album").querySelectorAll('input[type="text"]')) {
    const maps_ = maps(el.value);
    el.classList.toggle("hit", terms.some((term) => maps_.some((m) => m.folded.includes(term))));
  }
}

function deleteTrack(plan, track, button) {
  const message = `Delete “${track.artist} – ${track.title}”?\n\nThe file is removed and the remaining tracks are renumbered.\nIf the video is still in the playlist, a later update fetches it again.`;
  if (confirm(message)) submit("delete_track", { id: plan.source_id, video_id: track.video_id }, button);
}

function deleteAlbum(plan, button) {
  const n = plan.tracks.length;
  const message = `Delete the album “${plan.albumartist} — ${plan.album}”?\n\n${n} track(s), the cover and the album data are removed from\n${plan.folder}\n\nFiles you put there yourself are kept.`;
  if (confirm(message)) {
    submit("delete_album", { id: plan.source_id }, button).then(() => closeAlbum(null));
  }
}

function pruneAlbum(p, gone, button) {
  const list = gone.map((t) => `  ${t.number}. ${t.artist} – ${t.title}`).join("\n");
  if (confirm(`Delete these files? They are no longer in the YouTube playlist:\n\n${list}`)) submit("prune", { id: p.source_id }, button);
}

// keeps tenths when there are any, so a value set on the player survives a save from the field
const asTime = (seconds) => {
  if (seconds == null) return "";
  const rest = seconds % 60;
  const shown = Number.isInteger(rest) ? String(rest).padStart(2, "0") : rest.toFixed(1).padStart(4, "0");
  return `${Math.floor(seconds / 60)}:${shown}`;
};

const fromTime = (text) => {
  const parts = String(text).trim().split(":");
  if (!text.trim() || parts.some((p) => p.trim() === "" || isNaN(Number(p)))) return null;
  return parts.reduce((acc, p) => acc * 60 + Number(p), 0);
};

// MusicBrainz knows how long the song is: once a start is set, propose where it ends
// only when the known length actually fits this file: MusicBrainz often has another,
// longer version of the same song (live, extended), which would suggest past the end
const usableLength = (t) => t.mb_length && t.duration && t.mb_length < t.duration - 0.5;

function suggestEnd(startInput, track) {
  const end = startInput.closest("tr").querySelector("[name=trim_end]");
  if (!usableLength(track) || (end.value && end.dataset.suggested !== "1")) return;
  const start = fromTime(startInput.value);
  if (start == null) return;
  end.value = asTime(start + track.mb_length);
  end.dataset.suggested = "1";
  end.title = "suggested from the MusicBrainz length — change it if it cuts too early";
}

function trimChannel(track, button) {
  const row = button.closest("tr");
  const start = row.querySelector("[name=trim_start]").value;
  const end = row.querySelector("[name=trim_end]").value;
  const what = start || end ? `cut ${start || "0:00"}–${end || "end"}` : "remove the trim";
  if (confirm(`Apply to every track from “${track.channel}” in the library: ${what}?`)) {
    submit("trim_channel", { channel: track.channel, start, end }, button);
  }
}

// a track without a separate audio stream: explain, then let the user decide
function askAudioChoice(plan, track, button) {
  const detail = (track.error || "").replace(/^YouTube offers no separate audio stream \(?/, "").replace(/\)$/, "");
  const message = [
    `“${track.artist} – ${track.title}”`,
    "",
    `Twice in a row, all YouTube offered was: ${detail || "a combined video stream"}.`,
    "Usually that means an old or low-quality upload that never had a separate audio track.",
    "",
    "But it can also be temporary — YouTube sometimes withholds the audio formats for a while.",
    "If this is a normal, recent video, close this and press “Re-check source” first:",
    "a later attempt often gets the full-quality Opus.",
    "",
    "OK: take the audio out of that video. It is copied, not re-encoded, and saved as .m4a —",
    "the best available here, but audibly below your other tracks, and the choice sticks.",
    "",
    "Cancel: leave the track out. You can decide later; nothing is lost.",
  ].join("\n");
  if (confirm(message)) {
    submit("edit", { id: plan.source_id, edits: { tracks: [{ video_id: track.video_id, audio_choice: "combined" }] } }, button);
  }
}

function saveAlbum(ev) {
  ev.preventDefault();
  const form = ev.target;
  const edits = {
    album: form.album.value, albumartist: form.albumartist.value, year: form.year.value,
    tracks: [...form.querySelectorAll("tbody tr")].map((tr) => ({
      video_id: tr.dataset.id,
      artist: tr.querySelector("[name=artist]").value,
      title: tr.querySelector("[name=title]").value,
      trim_start: tr.querySelector("[name=trim_start]").value,
      trim_end: tr.querySelector("[name=trim_end]").value,
      disc: tr.querySelector("[name=disc]")?.value,
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
$("#artist-all").addEventListener("click", () => showArtist(null));
$("#artist-update").addEventListener("click", (e) => submit("update", { artist: artistFilter, deep: e.shiftKey }, e.currentTarget));
$("#artist-new").addEventListener("click", async (e) => {
  const id = await submit("open", { q: artistFilter }, e.currentTarget);
  if (id) {
    waitingFor = id;
    fill($("#results"), h("p", { class: "muted" }, `Looking for albums by “${artistFilter}” …`));
    $("#results").hidden = false;
  }
});

// "/" jumps to the library filter, the way it does in most things that have one
document.addEventListener("keydown", (e) => {
  if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey || e.target?.closest?.("input, select, textarea")) return;
  e.preventDefault();
  $("#libfilter").focus();
  $("#libfilter").select();
});

// Escape closes whichever panel is open, and the album editor gives focus back to its tile
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || e.target?.closest?.("input, select, textarea")) return;
  if (!$("#album").hidden) closeAlbum();
  else if (!$("#results").hidden) { $("#results").hidden = true; clearTimeout(detailTimer); }
  else if (!$("#settings").hidden) $("#settings").hidden = true;
  else if (artistFilter) showArtist(null);
});

// -- player ----------------------------------------------------------------------------

const audio = $("#audio");
let queue = []; // [{ album, video_id, title, artist }]
let qi = -1;

const fmt = (sec) => (Number.isFinite(sec) ? `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, "0")}` : "0:00");
const isPlaying = (albumId, videoId) => qi >= 0 && queue[qi].album === albumId && queue[qi].video_id === videoId;

async function playAlbum(albumId, start = 0) {
  markAlbum(albumId);
  let plan;
  try {
    plan = currentAlbum?.source_id === albumId ? currentAlbum : await api(`/api/album?id=${encodeURIComponent(albumId)}`);
  } catch (e) {
    return toast(e.message, "failed");
  }
  queue = plan.tracks.filter((t) => t.state === "done").map((t) => ({
    album: albumId, video_id: t.video_id, title: t.title, artist: t.artist, albumName: plan.album,
    start: t.trim_start, end: t.trim_end, duration: t.duration, mb_length: t.mb_length,
  }));
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
  renderTrim();
  if ("mediaSession" in navigator) {
    navigator.mediaSession.metadata = new MediaMetadata({ title: t.title, artist: t.artist, album: t.albumName,
      artwork: [{ src: `/api/cover?id=${encodeURIComponent(t.album)}` }] });
  }
}

// the length often arrives only with the file (older albums have none in the plan)
audio.addEventListener("loadedmetadata", renderTrim);
audio.addEventListener("durationchange", renderTrim);
audio.addEventListener("play", () => { $("#p-play").textContent = "⏸"; });
audio.addEventListener("pause", () => { $("#p-play").textContent = "▶"; });
audio.addEventListener("ended", () => (qi + 1 < queue.length ? playIndex(qi + 1) : null));
audio.addEventListener("timeupdate", () => {
  const t = queue[qi];
  if (t) {  // preview the trim while listening: skip the head, stop at the end
    if (t.start && audio.currentTime < t.start - 0.4 && !dragging) audio.currentTime = t.start;
    if (t.end && audio.currentTime > t.end) (qi + 1 < queue.length ? playIndex(qi + 1) : audio.pause());
  }
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

// -- trim handles on the player ----------------------------------------------------------

let dragging = null;

function trimLimit() {
  const t = queue[qi];
  const total = t?.duration || audio.duration;
  return Number.isFinite(total) && total > 0 ? total : 0;
}

function renderTrim() {
  const t = queue[qi];
  const total = trimLimit();
  const show = Boolean(t && total);
  for (const id of ["#p-keep", "#p-h-start", "#p-h-end"]) $(id).hidden = !show;
  $("#p-trim-actions").hidden = !show;
  if (!show) return;
  const start = t.start || 0;
  const end = t.end == null ? total : t.end;
  $("#p-keep").style.left = `${(start / total) * 100}%`;
  $("#p-keep").style.right = `${100 - (end / total) * 100}%`;
  $("#p-h-start").style.left = `${(start / total) * 100}%`;
  $("#p-h-end").style.left = `${(end / total) * 100}%`;
  $("#p-h-start").title = `Song starts at ${fmt(start)}`;
  $("#p-h-end").title = `Song ends at ${fmt(end)}`;
  syncTrimInputs(t);
}

// the text fields in the album view are the same value: keep them in step
function syncTrimInputs(t) {
  if (currentAlbum?.source_id !== t.album) return;
  const row = document.querySelector(`#album tr[data-id="${CSS.escape(t.video_id)}"]`);
  if (!row) return;
  row.querySelector("[name=trim_start]").value = t.start == null ? "" : asTime(t.start);
  row.querySelector("[name=trim_end]").value = t.end == null ? "" : asTime(t.end);
}

function setTrim(which, seconds) {
  const t = queue[qi];
  if (!t) return;
  const total = trimLimit();
  const value = Math.min(Math.max(seconds, 0), total);
  if (which === "start") t.start = value >= (t.end ?? total) ? t.start : value || null;
  else t.end = value <= (t.start || 0) ? t.end : value >= total ? null : value;
  renderTrim();
}

for (const [id, which] of [["#p-h-start", "start"], ["#p-h-end", "end"]]) {
  $(id).addEventListener("pointerdown", (e) => {
    dragging = which;
    e.currentTarget.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  $(id).addEventListener("pointermove", (e) => {
    if (dragging !== which) return;
    const rect = $("#p-trim").getBoundingClientRect();
    setTrim(which, ((e.clientX - rect.left) / rect.width) * trimLimit());
  });
  $(id).addEventListener("pointerup", (e) => {
    dragging = null;
    e.currentTarget.releasePointerCapture(e.pointerId);
    const t = queue[qi];
    if (t) audio.currentTime = Math.max((which === "start" ? t.start || 0 : (t.end || trimLimit()) - 3), 0);
  });
  $(id).addEventListener("keydown", (e) => {  // arrows for fine adjustment
    const step = e.shiftKey ? 1 : 0.1;
    const t = queue[qi];
    if (!t || !["ArrowLeft", "ArrowRight"].includes(e.key)) return;
    const current = which === "start" ? t.start || 0 : t.end ?? trimLimit();
    setTrim(which, current + (e.key === "ArrowRight" ? step : -step));
    e.preventDefault();
  });
}

$("#p-set-start").addEventListener("click", () => setTrim("start", audio.currentTime));
$("#p-set-end").addEventListener("click", () => setTrim("end", audio.currentTime));
$("#p-trim-clear").addEventListener("click", () => {
  const t = queue[qi];
  if (!t) return;
  t.start = t.end = null;
  renderTrim();
});
$("#p-trim-save").addEventListener("click", (e) => {
  const t = queue[qi];
  if (!t) return;
  submit("edit", { id: t.album, edits: { tracks: [{ video_id: t.video_id, trim_start: t.start == null ? "" : String(t.start), trim_end: t.end == null ? "" : String(t.end) }] } }, e.currentTarget);
});

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
// plain click: cheap check (one request per album); with shift: read every album fully
$("#update").addEventListener("click", (e) => submit("update", { deep: e.shiftKey }, e.currentTarget));

if ("serviceWorker" in navigator && window.isSecureContext) navigator.serviceWorker.register("/sw.js").catch(() => {});
poll();
