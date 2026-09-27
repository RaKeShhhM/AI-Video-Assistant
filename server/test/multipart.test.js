import assert from "node:assert/strict";
import test from "node:test";
import { uploadHarness, field, file, ending } from "../test-support/uploadHarness.js";

test("malformed multipart terminates safely, cleans files and permits the next upload", { timeout: 15000 }, async t => {
  const h = await uploadHarness(t);
  const cases = [
    { body: "bad", options: { type: "multipart/form-data" } },
    { body: file() }, // missing closing boundary after an opened file
    { body: `--s4-boundary\r\ninvalid-header\r\n\r\nx\r\n${ending}` },
    { body: `${field("language[x]")}${ending}` },
    { body: `${field("language[999999999]")}${ending}` },
    { body: `${field("language" + "[x]".repeat(20))}${ending}` },
    { body: `${field("x".repeat(101))}${ending}` },
    { body: `${field("language", "x".repeat(4097))}${ending}` },
    { body: `${Array.from({ length: 5 }, (_, i) => field(`field${i}`)).join("")}${ending}` },
    { body: Buffer.concat([file(32, "wrong-file-field"), Buffer.from(ending)]) },
    { body: Buffer.concat([file(), file(), Buffer.from(ending)]) },
  ];
  for (const item of cases) {
    const before = h.accepted();
    const response = await h.raw(item.body, item.options);
    assert.equal(response.status, 400, JSON.stringify(response));
    assert.equal(h.accepted(), before, "invalid request reached the controller");
    assert.doesNotMatch(response.body.message, /node_modules|clip.wav|Error:|reel-api-uploads/);
    await h.clean();
    assert.equal((await h.raw(Buffer.concat([file(), Buffer.from(ending)]))).status, 200);
    await h.clean();
  }
});

test("chunked uploads enforce inclusive file limits and accept exactly five parts", async t => {
  const h = await uploadHarness(t);
  for (const [size, status] of [[1023, 200], [1024, 200], [1025, 413], [4096, 413]]) {
    assert.equal((await h.raw(Buffer.concat([file(size), Buffer.from(ending)]))).status, status);
    await h.clean();
  }
  const fields = ["language", "a", "b", "c"].map(name => field(name)).join("");
  assert.equal((await h.raw(Buffer.concat([Buffer.from(fields), file(), Buffer.from(ending)]))).status, 200);
  await h.clean();
});
