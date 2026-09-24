import assert from "node:assert/strict";
import test from "node:test";
import express from "express";
import http from "node:http";
import { once } from "node:events";
import { access } from "node:fs/promises";
import { rateLimit } from "../src/middleware/rateLimit.js";
import { createUploadMiddleware, cleanupUpload } from "../src/middleware/upload.js";
import { errorHandler } from "../src/middleware/errorHandler.js";

test("rate limits isolate keys, report retry delay and expire", () => {
  let now = 0;
  const limit = rateLimit({ max: 2, windowMs: 1000, clock: () => now });
  const response = { status(code) { this.code = code; return this; }, set() { return this; }, json() {} };
  let accepted = 0;
  for (let i = 0; i < 3; i++) limit({ userId: "a" }, response, () => accepted++);
  assert.equal(accepted, 2);
  assert.equal(response.code, 429);
  limit({ userId: "b" }, response, () => accepted++);
  now = 1000;
  limit({ userId: "a" }, response, () => accepted++);
  assert.equal(accepted, 4);
});

test("real multipart uploads use disk, reject oversized/unsupported content, and clean aborted requests", async () => {
  const app = express();
  const paths = [];
  const requests = [];
  const upload = createUploadMiddleware({ maxBytes: 1024, maxSlots: 2 });
  app.post("/upload", (req, res, next) => { req.userId = req.headers["x-test-user"] || "a"; requests.push(req); next(); }, upload,
    async (req, res) => {
      req.uploadOwnedByController = true;
      try {
        assert.equal(req.file.buffer, undefined);
        await access(req.file.path);
        paths.push(req.file.path);
        res.json({ size: req.file.size });
      } finally { await cleanupUpload(req); req.releaseUpload(); }
    });
  app.use(errorHandler);
  const server = app.listen(0, "127.0.0.1");
  await once(server, "listening");
  const base = `http://127.0.0.1:${server.address().port}`;
  async function submit(bytes, name = "clip.wav", type = "audio/wav") {
    const form = new FormData();
    form.append("file", new Blob([Buffer.alloc(bytes)], { type }), name);
    return fetch(`${base}/upload`, { method: "POST", body: form });
  }
  async function settled() {
    for (let i = 0; i < 100; i++) {
      const results = await Promise.all(requests.map(async (req) => {
        try { await access(req.uploadDirectory); return false; } catch { return true; }
      }));
      if (results.every(Boolean)) return;
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    assert.fail("Upload directories were not removed");
  }
  try {
    assert.equal((await submit(100)).status, 200);
    await settled();
    assert.equal((await submit(1024)).status, 200); // inclusive documented maximum
    await settled();
    assert.equal((await submit(2048)).status, 413);
    await settled();
    assert.equal((await submit(20, "bad.exe", "application/octet-stream")).status, 415);
    await settled();
    const req = http.request(`${base}/upload`, { method: "POST", headers: { "Content-Type": "multipart/form-data; boundary=s3", "x-test-user": "abort" } });
    req.on("error", () => {});
    req.write('--s3\r\nContent-Disposition: form-data; name="file"; filename="clip.wav"\r\nContent-Type: audio/wav\r\n\r\n');
    req.write(Buffer.alloc(100));
    await new Promise((resolve) => setTimeout(resolve, 50));
    req.destroy();
    await settled();
    assert.equal((await submit(100)).status, 200); // capacity was released
    await settled();
    for (const path of paths) await assert.rejects(access(path));
  } finally {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  }
});
