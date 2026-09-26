// The length arithmetic, against the same table tests/test_length.py uses for plan.trimmed_gap.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { LENGTH, asTime, fmt, lengthBand, roundMark, markedTrim, trimTarget } from "../../src/ytalbum/webui/logic.mjs";

const table = JSON.parse(readFileSync(new URL("../shared/trim_target.json", import.meta.url))).cases;

test("trimTarget agrees with the shared table (plan.trimmed_gap's twin)", () => {
  for (const c of table) {
    const track = { duration: c.duration ?? null, mb_length: c.mb ?? null, lyrics_length: c.lrclib ?? null,
                    file_length: c.file_length ?? null };
    const got = trimTarget(track, track.duration, c.start, c.end);
    assert.equal(got.kept, c.kept, c.why);
    assert.equal(got.gap, c.gap, c.why);
  }
});

test("the bands are the chip's, and a stub outranks a wide gap", () => {
  assert.equal(lengthBand(240, 240), "close");
  assert.equal(lengthBand(240 + LENGTH.slack, 240), "close");      // exactly the slack is still close
  assert.equal(lengthBand(240 + LENGTH.slack + 0.1, 240), "slack");
  assert.equal(lengthBand(240 + LENGTH.big + 1, 240), "big");
  assert.equal(lengthBand(100, 240), "stub");                       // far too short to be the song
  assert.equal(lengthBand(240, null), null);                        // nobody knows: no verdict
});

test("a mark is a tenth of a second, inside the file", () => {
  assert.equal(roundMark(217.6449, 300), 217.6);
  assert.equal(roundMark(-5, 300), 0);
  assert.equal(roundMark(9999, 300), 300);
});

test("a mark that would cross the other one is refused", () => {
  assert.deepEqual(markedTrim({ start: null, end: 100 }, "start", 120, 300), { start: null, end: 100 });
  assert.deepEqual(markedTrim({ start: 50, end: null }, "end", 20, 300), { start: 50, end: null });
  assert.deepEqual(markedTrim({ start: null, end: null }, "end", 300, 300), { start: null, end: null }); // the end is the end
  assert.deepEqual(markedTrim({ start: null, end: null }, "start", 12.5, 300), { start: 12.5, end: null });
});

test("times read as a listener writes them", () => {
  assert.equal(asTime(null), "");
  assert.equal(asTime(65), "1:05");
  assert.equal(asTime(65.5), "1:05.5");
  assert.equal(fmt(65.9), "1:05");
  assert.equal(fmt(Infinity), "0:00");
});
