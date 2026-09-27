import assert from "node:assert/strict";
import express from "express";
import http from "node:http";
import { once } from "node:events";
import { access } from "node:fs/promises";
import { createUploadMiddleware, cleanupUpload } from "../src/middleware/upload.js";
import { errorHandler } from "../src/middleware/errorHandler.js";

export const boundary = "s4-boundary";
export const field = (name, value = "english") => `--${boundary}\r\nContent-Disposition: form-data; name="${name}"\r\n\r\n${value}\r\n`;
export const file = (bytes = 32, name = "file") => Buffer.concat([
  Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="${name}"; filename="clip.wav"\r\nContent-Type: audio/wav\r\n\r\n`),
  Buffer.alloc(bytes), Buffer.from("\r\n"),
]);
export const ending = `--${boundary}--\r\n`;

export async function until(check, message = "condition did not settle") {
  for (let attempt = 0; attempt < 200; attempt++) {
    if (await check()) return;
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  assert.fail(message);
}

export async function uploadHarness(t, { maxSlots = 1, handler } = {}) {
  const requests = [];
  let accepted = 0;
  const app = express();
  app.post("/upload", (req, res, next) => {
    req.userId = req.headers["x-test-user"] || "same-user";
    requests.push(req);
    next();
  }, createUploadMiddleware({ maxBytes: 1024, maxSlots }), async (req, res, next) => {
    accepted++;
    req.uploadOwnedByController = true;
    try {
      if (handler) await handler(req, res);
      else res.json({ size: req.file?.size, fields: req.body });
    } catch (error) { next(error); }
    finally { await cleanupUpload(req); req.releaseUpload(); }
  });
  app.use(errorHandler);
  const server = app.listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(async () => {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  });
  const url = `http://127.0.0.1:${server.address().port}/upload`;
  function raw(body, { type = `multipart/form-data; boundary=${boundary}`, user = "same-user" } = {}) {
    return new Promise((resolve, reject) => {
      // write + end deliberately uses chunked transport (no Content-Length).
      const request = http.request(url, { method: "POST", headers: { "Content-Type": type, "x-test-user": user } }, response => {
        const chunks = [];
        response.on("data", chunk => chunks.push(chunk));
        response.on("end", () => resolve({ status: response.statusCode, body: JSON.parse(Buffer.concat(chunks).toString()) }));
        response.on("error", reject);
      });
      request.setTimeout(3000, () => request.destroy(new Error("Upload response timed out")));
      request.on("error", reject);
      request.write(body);
      request.end();
    });
  }
  async function clean() {
    await until(async () => {
      for (const req of requests) {
        if (!req.uploadDirectory) continue;
        try { await access(req.uploadDirectory); return false; }
        catch (error) { if (error.code !== "ENOENT") throw error; }
      }
      return true;
    }, "temporary upload directories remain");
  }
  return { url, requests, raw, clean, accepted: () => accepted };
}
