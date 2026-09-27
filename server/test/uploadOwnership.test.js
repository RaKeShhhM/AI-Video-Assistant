import assert from "node:assert/strict";
import test from "node:test";
import { access } from "node:fs/promises";
import { uploadHarness, file, ending, until } from "../test-support/uploadHarness.js";

test("early responses retain files and admission until forwarding finishes", { timeout: 10000 }, async t => {
  const held = new Map();
  const h = await uploadHarness(t, { maxSlots: 2, handler: async (req, res) => {
    let release;
    const forwarding = new Promise(resolve => { release = resolve; });
    held.set(req.userId, { req, release });
    res.status(201).json({ accepted: true });
    await forwarding;
    await access(req.file.path); // response-close must not delete the file
  } });
  t.after(() => { for (const item of held.values()) item.release(); });
  const body = Buffer.concat([file(), Buffer.from(ending)]);
  assert.equal((await h.raw(body, { user: "a" })).status, 201);
  await access(held.get("a").req.file.path);
  assert.equal((await h.raw(body, { user: "a" })).status, 429); // per-user gate
  assert.equal((await h.raw(body, { user: "b" })).status, 201);
  assert.equal((await h.raw(body, { user: "c" })).status, 429); // global gate
  held.get("a").release();
  await until(async () => {
    try { await access(held.get("a").req.uploadDirectory); return false; }
    catch (error) { if (error.code !== "ENOENT") throw error; return true; }
  });
  await access(held.get("b").req.file.path); // another user's cleanup is isolated
  assert.equal((await h.raw(body, { user: "a" })).status, 201);
  for (const item of held.values()) item.release();
  await h.clean();
});
