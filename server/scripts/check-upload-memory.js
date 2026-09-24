// Isolated upload transport check: no database, AI service or provider calls.
import assert from "node:assert/strict";
import express from "express";
import http from "node:http";
import { once } from "node:events";
import { createUploadMiddleware, cleanupUpload } from "../src/middleware/upload.js";
import { errorHandler } from "../src/middleware/errorHandler.js";

const bytes = 64 * 1024 * 1024;
const app = express();
let arrived = 0;
let release;
const bothUploaded = new Promise((resolve) => { release = resolve; });
app.post("/upload", (req, res, next) => { req.userId = req.headers["x-test-user"]; next(); },
  createUploadMiddleware({ maxBytes: bytes, maxSlots: 2 }), async (req, res) => {
    req.uploadOwnedByController = true;
    try {
      assert.equal(req.file.size, bytes);
      assert.equal(req.file.buffer, undefined);
      if (++arrived === 2) release();
      await bothUploaded;
      await new Promise((resolve) => setTimeout(resolve, 50));
      res.json({ ok: true });
    } finally { await cleanupUpload(req); req.releaseUpload(); }
  });
app.use(errorHandler);
const server = app.listen(0, "127.0.0.1");
await once(server, "listening");
const baseline = process.memoryUsage().rss;
let peak = baseline;
const sampler = setInterval(() => { peak = Math.max(peak, process.memoryUsage().rss); }, 5);

async function upload(user) {
  const prefix = Buffer.from('--s3\r\nContent-Disposition: form-data; name="file"; filename="load.wav"\r\nContent-Type: audio/wav\r\n\r\n');
  const suffix = Buffer.from("\r\n--s3--\r\n");
  const req = http.request({ hostname: "127.0.0.1", port: server.address().port, path: "/upload", method: "POST",
    headers: { "x-test-user": user, "Content-Type": "multipart/form-data; boundary=s3", "Content-Length": prefix.length + bytes + suffix.length } });
  const response = new Promise((resolve, reject) => {
    req.on("error", reject);
    req.on("response", (res) => { res.resume(); res.on("end", () => resolve(res.statusCode)); });
  });
  req.write(prefix);
  const chunk = Buffer.alloc(65536); // reused; client does not allocate 64 MB
  for (let sent = 0; sent < bytes; sent += chunk.length) {
    if (!req.write(chunk)) await once(req, "drain");
  }
  req.end(suffix);
  assert.equal(await response, 200);
}

try {
  await Promise.all([upload("a"), upload("b")]);
  const delta = (peak - baseline) / 1024 / 1024;
  console.log(JSON.stringify({ concurrentUploads: 2, eachMiB: 64, baselineRssMiB: +(baseline / 1024 / 1024).toFixed(1),
    peakRssMiB: +(peak / 1024 / 1024).toFixed(1), growthMiB: +delta.toFixed(1), guardMiB: 96 }));
  assert.ok(delta < 96, "Upload RSS growth exceeded the 96 MiB test guard");
} finally {
  clearInterval(sampler);
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}
