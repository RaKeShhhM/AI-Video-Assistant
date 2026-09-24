import multer from "multer";
import { createWriteStream } from "node:fs";
import { mkdir, mkdtemp, unlink, rmdir } from "node:fs/promises";
import { pipeline } from "node:stream/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { limits, mediaExtensions } from "../config/resourceLimits.js";
import { ApiError } from "../utils/ApiError.js";

export const uploadRoot = path.join(tmpdir(), "reel-api-uploads");

export function cleanupUpload(req) {
  if (!req.uploadDirectory) return Promise.resolve();
  // Abort, response-close and Multer errors can arrive together on Windows.
  // Share one cleanup operation instead of racing multiple unlinks.
  req.uploadCleanup ??= removeUpload(req);
  return req.uploadCleanup;
}

async function removeUpload(req) {
  await req.uploadWrite?.catch(() => {});
  const directory = req.uploadDirectory;
  if (!directory) return;
  // Delete only the one server-generated file and its immediate empty folder.
  if (path.dirname(path.resolve(directory)) !== path.resolve(uploadRoot)
      || !path.basename(directory).startsWith("upload-")) throw new Error("Invalid upload cleanup path");
  if (req.uploadPath && path.dirname(req.uploadPath) === directory) {
    await unlink(req.uploadPath).catch((err) => { if (err.code !== "ENOENT") throw err; });
  }
  await rmdir(directory).catch((err) => { if (err.code !== "ENOENT") throw err; });
}

export function createUploadMiddleware({ maxBytes = limits.uploadBytes, maxSlots = limits.uploadSlots } = {}) {
  let slots = 0;
  const users = new Set();
  const storage = {
    _handleFile(req, file, cb) {
      const destination = path.join(req.uploadDirectory, `source${path.extname(file.originalname).toLowerCase()}`);
      req.uploadPath = destination;
      const output = createWriteStream(destination, { flags: "wx" });
      const abort = () => file.stream.destroy(new Error("Upload interrupted"));
      req.once("aborted", abort);
      req.uploadWrite = pipeline(file.stream, output);
      req.uploadWrite.then(() => cb(null, { path: destination, size: output.bytesWritten }), cb)
        .finally(() => req.off("aborted", abort));
    },
    _removeFile(req, file, cb) {
      unlink(file.path).then(() => cb(null), (err) => cb(err.code === "ENOENT" ? null : err));
    },
  };
  const parse = multer({ storage, limits: {
    // Busboy signals its limit at equality; one extra byte lets files exactly
    // at the documented maximum succeed while keeping the bound explicit.
    fileSize: maxBytes + 1, files: 1, fields: 4, parts: 5, fieldSize: 4096, fieldNameSize: 100, headerPairs: 100,
  }, fileFilter(req, file, cb) {
    const ext = path.extname(file.originalname).toLowerCase();
    if (!mediaExtensions.includes(ext) || !(file.mimetype.startsWith("audio/") || file.mimetype.startsWith("video/")
        || file.mimetype === "application/octet-stream" || file.mimetype === "application/ogg")) {
      return cb(new ApiError(415, "Unsupported media type. Use MP4, M4A, MOV, MKV, WebM, MP3, WAV, OGG, FLAC or AAC."));
    }
    cb(null, true);
  } }).single("file");

  return async (req, res, next) => {
    if (Number(req.headers["content-length"]) > maxBytes + 65536) return next(new ApiError(413, "Upload is too large."));
    const user = String(req.userId);
    if (slots >= maxSlots || users.has(user)) return next(new ApiError(429, "An upload is already in progress or the server is busy. Try again later."));
    slots++;
    users.add(user);
    let released = false;
    req.releaseUpload = () => {
      if (!released) { released = true; slots--; users.delete(user); }
    };
    const cleanup = () => cleanupUpload(req).catch(() => console.error("Temporary upload cleanup failed"))
      .finally(req.releaseUpload);
    req.once("aborted", () => { void cleanup(); });
    res.once("close", () => { if (!req.uploadOwnedByController) void cleanup(); });
    try {
      await mkdir(uploadRoot, { recursive: true });
      req.uploadDirectory = await mkdtemp(path.join(uploadRoot, "upload-"));
      if (req.aborted) { await cleanup(); return; }
      parse(req, res, async (err) => {
        if (err) { await cleanup(); if (!req.aborted) return next(err); return; }
        if (req.file?.size > maxBytes) { await cleanup(); return next(new ApiError(413, "File exceeds the upload size limit.")); }
        next();
      });
    } catch (err) { await cleanup(); next(err); }
  };
}

export const uploadMedia = createUploadMiddleware();
