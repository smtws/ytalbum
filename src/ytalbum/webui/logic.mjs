// The part of the page that is arithmetic rather than DOM: matching text, reading lengths,
// arranging tracks, deciding what a panel offers. It is a module so `node --test` can import it
// (tests/js/), and the page imports it with `<script type="module">` — no build step either way.
//
// Nothing in here touches `document`. Where a rule has a twin in Python, the twin is named in the
// comment and `tests/shared/*.json` holds the table both sides are tested against, so the two
// cannot drift apart (DESIGN.md §9.33).

// -- text: folding for the library filter ------------------------------------------------

const LETTERS = { ð: "d", þ: "th", ø: "o", æ: "ae", œ: "oe", ß: "ss", ł: "l", đ: "d", ŋ: "n", ʒ: "z" };

// Folds one character at a time and remembers where each folded character came from, so a
// match can be pointed back at the original text ("ü" -> "ue" is two characters from one).
export function foldMap(text, german) {
  let folded = "";
  const from = [];
  for (let i = 0; i < (text || "").length; i++) {
    let c = text[i].normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
    if (german && (c === "a" || c === "o" || c === "u") && text[i].normalize("NFD").length > 1) c += "e";
    c = c.replace(/[ðþøæœßłđŋʒ]/g, (x) => LETTERS[x]).replace(/[^\p{L}\p{N}]+/gu, " ");
    for (const _ of c) from.push(i);
    folded += c;
  }
  return { folded, from };
}

export const fold = (s) => foldMap(s, false).folded;

// German keyboards without umlauts write "knueppel" for "Knüppel", which folding to
// "knuppel" would miss — so every string is matched (and highlighted) in both spellings.
export const maps = (text) => [foldMap(text, false), foldMap(text, true)];

export const hits = (terms, text) => {
  const both = maps(text);
  return terms.every((term) => both.some((m) => m.folded.includes(term)));
};

// -- time ---------------------------------------------------------------------------------

export const asTime = (seconds) => {
  if (seconds == null) return "";
  const rest = seconds % 60;
  const shown = Number.isInteger(rest) ? String(rest).padStart(2, "0") : rest.toFixed(1).padStart(4, "0");
  return `${Math.floor(seconds / 60)}:${shown}`;
};

export const fmt = (sec) =>
  Number.isFinite(sec) ? `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, "0")}` : "0:00";

// -- lengths: the ⏱ chip and the trim target ----------------------------------------------

// Mirrors plan.py's LENGTH_SLACK / LENGTH_BIG / LENGTH_STUB — change a number there and here.
export const LENGTH = { slack: 5, big: 20, stub: 0.6 };

export const refLength = (t) => t.mb_length || t.lyrics_length || null;
export const ourLength = (t) => t.file_length || (t.duration ? (t.trim_end || t.duration) - (t.trim_start || 0) : null);

// What the pending marks would leave, and how far that is from the length the song is said to
// be. Twin of plan.trimmed_gap; tests/shared/trim_target.json is the table both are tested on.
export function trimTarget(t, total, start, end) {
  if (!total) return { kept: null, gap: null, ref: null };
  const kept = (end == null ? total : Math.min(end, total)) - (start || 0);
  const ref = refLength(t);
  return { kept, gap: ref ? kept - ref : null, ref };
}

// Which band a length gap falls in, which is the chip's colour and the target line's.
export function lengthBand(kept, ref) {
  if (!ref || kept == null) return null;
  if (kept < ref * LENGTH.stub) return "stub";
  const off = Math.abs(kept - ref);
  return off > LENGTH.big ? "big" : off > LENGTH.slack ? "slack" : "close";
}

// A mark is a tenth of a second: `currentTime` carries a dozen decimals of mouse precision that
// mean nothing musically and would end up in the plan and on ffmpeg's command line.
export const roundMark = (seconds, total) => Math.round(Math.min(Math.max(seconds, 0), total) * 10) / 10;

// A typed mark is refused when it would cross the other one — the start cannot sit after the end.
export function markedTrim({ start, end }, which, value, total) {
  if (which === "start") return { start: value >= (end ?? total) ? start : value || null, end };
  return { start, end: value <= (start || 0) ? end : value >= total ? null : value };
}

// -- arranging tracks ----------------------------------------------------------------------

// Each disc counts from 1 again, so the position column never shows two 3s mid-edit. Twin of
// service.arrange (which does the same to a saved plan).
export function numberByDisc(discs) {
  const counts = new Map();
  return discs.map((d) => {
    const disc = Number(d) || 1;
    counts.set(disc, (counts.get(disc) || 0) + 1);
    return counts.get(disc);
  });
}

// A row dropped among another disc's rows joins that disc, at that position. `rows` is
// [{id, disc}] in the order they stand; the result is the new order, with the moved row's disc
// taken from whichever neighbour it now sits beside (the one above, or the one below when it
// landed first). What the server then does with it is service.placed (§9.32).
export function movedRow(rows, id, overId) {
  const from = rows.findIndex((r) => r.id === id);
  const to = rows.findIndex((r) => r.id === overId);
  if (from < 0 || to < 0 || from === to) return rows;
  const moved = { ...rows[from] };
  const rest = rows.filter((_, i) => i !== from);
  const at = to > from ? rows.filter((_, i) => i !== from).findIndex((r) => r.id === overId) + 1
                       : rest.findIndex((r) => r.id === overId);
  rest.splice(at, 0, moved);
  const neighbour = rest[at - 1] ?? rest[at + 1];
  if (neighbour) moved.disc = neighbour.disc;
  return rest;
}

// -- what a panel offers --------------------------------------------------------------------

// The lyrics panel, from what /api/lyrics answered: what the header says, whose the words are,
// and which actions may be offered. The rules are §9.21's — the user's words are not lrclib's to
// replace, and there is nothing to reject when no entry was matched.
export function lyricsPanelState(d) {
  const mine = d.owner === "user";
  const words = Boolean(d.text);
  return {
    where: words ? (d.status === "synced" ? "with timestamps" : "plain text") : "no words yet",
    ownership: mine ? "yours" : words && d.lrclib_id ? `lrclib #${d.lrclib_id}` : null,
    actions: [
      words ? "Edit" : "Write lyrics",
      ...(mine ? [] : ["Look up again"]),
      ...(!mine && d.lrclib_id ? ["Not these words"] : []),
    ],
  };
}

// The badge on a field: a button back to what ytalbum derived, a plain badge, or nothing.
// Nothing is offered where nothing was derived — an album from before `auto` was recorded has
// no value to go back to (§9.29).
export function resetKind(provenance, derived) {
  if (provenance !== "user") return provenance ? "badge" : null;
  return derived === undefined || derived === null || derived === "" ? "badge" : "button";
}
