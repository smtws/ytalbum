// Arranging tracks in the album view: the numbers the column shows while you drag, and where a
// dropped row lands. The server's half of the same rules is service.placed / service.arrange.
import { test } from "node:test";
import assert from "node:assert/strict";

import { movedRow, numberByDisc } from "../../src/ytalbum/webui/logic.mjs";

const rows = (...discs) => discs.map((disc, i) => ({ id: `t${i + 1}`, disc: String(disc) }));
const order = (list) => list.map((r) => `${r.disc}-${r.id}`).join(" ");

test("each disc counts from 1 again", () => {
  assert.deepEqual(numberByDisc(["1", "1", "1"]), [1, 2, 3]);
  assert.deepEqual(numberByDisc(["1", "1", "2", "2", "2"]), [1, 2, 1, 2, 3]);
  assert.deepEqual(numberByDisc([]), []);
  assert.deepEqual(numberByDisc(["", null, "x"]), [1, 2, 3]);  // anything unreadable is disc 1
});

test("a row dragged down lands after the row it was dropped on", () => {
  const before = rows(1, 1, 1, 1);
  assert.equal(order(movedRow(before, "t1", "t3")), "1-t2 1-t3 1-t1 1-t4");
});

test("a row dragged up lands before it", () => {
  const before = rows(1, 1, 1, 1);
  assert.equal(order(movedRow(before, "t4", "t2")), "1-t1 1-t4 1-t2 1-t3");
});

test("dropped among another disc's rows, it joins that disc", () => {
  const before = rows(1, 1, 2, 2);          // t1 t2 | t3 t4
  const after = movedRow(before, "t3", "t1"); // drop disc 2's first row on disc 1's first
  assert.equal(order(after), "1-t3 1-t1 1-t2 2-t4");
  assert.deepEqual(numberByDisc(after.map((r) => r.disc)), [1, 2, 3, 1]);
});

test("a row dropped at the very top takes the disc of the row below it", () => {
  const before = rows(1, 1, 2, 2);
  assert.equal(order(movedRow(before, "t4", "t1")), "1-t4 1-t1 1-t2 2-t3");
});

test("dropping a row on itself, or on nothing, changes nothing", () => {
  const before = rows(1, 1, 2);
  assert.equal(movedRow(before, "t2", "t2"), before);
  assert.equal(movedRow(before, "t2", "nope"), before);
  assert.equal(movedRow(before, "gone", "t1"), before);
});

test("dragging up puts the row before the one it was dropped on, disc and all", () => {
  const before = rows(1, 2, 2);                 // t1 | t2 t3
  assert.equal(order(movedRow(before, "t3", "t1")), "1-t3 1-t1 2-t2");
});
