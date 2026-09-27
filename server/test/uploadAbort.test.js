import assert from "node:assert/strict";
import test from "node:test";
import http from "node:http";
import { uploadHarness, boundary, file, ending, until } from "../test-support/uploadHarness.js";

test("repeated aborts before and during file writes close storage and release admission", { timeout: 15000 }, async t => {
  const h = await uploadHarness(t);
  for (const duringWrite of [false, true, true, false, true]) {
    const before = h.requests.length;
    const request = http.request(h.url, { method: "POST", headers: {
      "Content-Type": `multipart/form-data; boundary=${boundary}`,
      "Transfer-Encoding": "chunked",
    } });
    request.on("error", () => {});
    request.flushHeaders();
    if (duringWrite) request.write(file(64));
    await until(() => h.requests.length > before && (duringWrite
      ? Boolean(h.requests.at(-1).uploadWrite) : Boolean(h.requests.at(-1).uploadDirectory)));
    const received = h.requests.at(-1);
    const closed = new Promise(resolve => request.once("close", resolve));
    request.destroy();
    await closed;
    await until(() => received.aborted);
    await h.clean();
    assert.equal((await h.raw(Buffer.concat([file(), Buffer.from(ending)]))).status, 200);
    await h.clean();
  }
});


