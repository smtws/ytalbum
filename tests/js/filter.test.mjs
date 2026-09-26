// The library filter's folding: it must find a song whatever the keyboard could type.
import { test } from "node:test";
import assert from "node:assert/strict";

import { fold, foldMap, hits } from "../../src/ytalbum/webui/logic.mjs";

test("case, accents and punctuation are ignored", () => {
  assert.equal(fold("Njǫrð"), "njord");
  assert.equal(fold("Éclair!"), "eclair ");
  assert.equal(fold("AC/DC"), "ac dc");
});

test("the letters Unicode cannot fold are spelled out", () => {
  assert.equal(fold("Mötley Crüe"), "motley crue");
  // each character folds on its own, so ", " becomes two spaces — harmless, because the filter
  // splits what you type into words and asks for each of them separately (the test below)
  assert.equal(fold("Blöde Frage, Saufgelage"), "blode frage  saufgelage");
  assert.equal(fold("Þrúðheimr"), "thrudheimr");
});

test("a German keyboard without umlauts still finds the song", () => {
  assert.ok(hits(["knueppel"], "Knüppel"));
  assert.ok(hits(["knuppel"], "Knüppel"));   // and so does the plain folding
  assert.ok(hits(["saufgelage"], "Blöde Frage, Saufgelage"));
});

test("every folded character points back at the one it came from", () => {
  const { folded, from } = foldMap("Knüppel", true);
  assert.equal(folded, "knueppel");
  assert.equal(from.length, folded.length);
  assert.equal(from[2], 2);   // "ue" both came from the "ü"
  assert.equal(from[3], 2);
});

test("all terms must match, not just one", () => {
  assert.ok(hits(["dark", "lullabies"], "My Dark Lullabies"));
  assert.ok(!hits(["dark", "sunshine"], "My Dark Lullabies"));
});

test("the words of a query are asked for one by one, so punctuation between them is no barrier", () => {
  // what the page does with "1 heavy": fold, split, then every word must appear somewhere
  const terms = fold("1 heavy").split(" ").filter(Boolean);
  assert.ok(hits(terms, "Vol. 1 - Heavy Sleeping"));
  assert.ok(hits(fold("frage saufgelage").split(" ").filter(Boolean), "Blöde Frage, Saufgelage"));
});
