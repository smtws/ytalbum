// What a panel offers, which is where the ownership rules of §9.21 and §9.29 become visible.
import { test } from "node:test";
import assert from "node:assert/strict";

import { lyricsPanelState, resetKind } from "../../src/ytalbum/webui/logic.mjs";

test("lrclib's words can be looked up again or rejected", () => {
  const s = lyricsPanelState({ text: "[00:01.00] a", status: "synced", lrclib_id: 11, owner: null });
  assert.equal(s.where, "with timestamps");
  assert.equal(s.ownership, "lrclib #11");
  assert.deepEqual(s.actions, ["Edit", "Look up again", "Not these words"]);
});

test("the user's own words are not lrclib's to replace", () => {
  const s = lyricsPanelState({ text: "mine", status: "plain", lrclib_id: 11, owner: "user" });
  assert.equal(s.ownership, "yours");
  assert.deepEqual(s.actions, ["Edit"]);   // the editor's Delete is the way back
});

test("a track with no words offers writing them, and a lookup", () => {
  const s = lyricsPanelState({ text: "", status: "none", lrclib_id: null, owner: null });
  assert.equal(s.where, "no words yet");
  assert.equal(s.ownership, null);
  assert.deepEqual(s.actions, ["Write lyrics", "Look up again"]);
});

test("after a clear the kept lrclib id is not shown as if it had words", () => {
  // the id stays on the track (it is how a sidecar is recognised as ours) but there are no words
  const s = lyricsPanelState({ text: "", status: "none", lrclib_id: 11, owner: null });
  assert.equal(s.ownership, null);
  assert.deepEqual(s.actions, ["Write lyrics", "Look up again", "Not these words"]);
});

test("the badge is a button only when there is something to go back to", () => {
  assert.equal(resetKind("user", "Feuerschwanz"), "button");
  assert.equal(resetKind("user", undefined), "badge");   // an album from before `auto` was kept
  assert.equal(resetKind("user", ""), "badge");
  assert.equal(resetKind("mb", "anything"), "badge");
  assert.equal(resetKind(undefined, "anything"), null);  // nothing claims this value
});
