import { createHash, randomUUID } from "node:crypto";
import { createReadStream } from "node:fs";
import mongoose from "mongoose";
import Job from "../models/Job.js";
import { usage } from "./usageService.js";
import { cloneJobUpload, storeJobUpload } from "./jobMedia.js";
import { ApiError } from "../utils/ApiError.js";

const digest = value => createHash("sha256").update(value).digest("hex");
const INTAKE_LEASE_MS = 10 * 60 * 1000;
export const retentionDays = Number(process.env.JOB_RETENTION_DAYS || 30);
if (!Number.isSafeInteger(retentionDays) || retentionDays < 1 || retentionDays > 3650) {
  throw new Error("Invalid JOB_RETENTION_DAYS");
}

export function submissionKey(value) {
  if (typeof value !== "string" || !/^[A-Za-z0-9_-]{16,128}$/.test(value)) {
    throw new ApiError(400, "Idempotency-Key must contain 16–128 letters, digits, underscores or hyphens.");
  }
  return value;
}

export async function sourceFingerprint(source) {
  let content = source.youtubeUrl;
  if (source.file) {
    const hash = createHash("sha256");
    for await (const chunk of createReadStream(source.file.path)) hash.update(chunk);
    content = hash.digest("hex");
  } else if (source.artifact) content = source.artifact.sha256;
  return digest(JSON.stringify([source.type, content, source.file?.originalname || source.artifact?.filename,
    source.language || "english", String(source.retryOf || "")]));
}

// A non-runnable intent precedes quota/media writes. A lost response is retried
// against the same durable identity; it is never treated as definite rejection.
export async function submitJob(user, key, source) {
  const _id = new mongoose.Types.ObjectId(digest(JSON.stringify([String(user), key])).slice(0, 24));
  const fingerprint = await sourceFingerprint(source);
  const now = new Date();
  let job;
  try {
    job = await Job.findOneAndUpdate({ _id }, { $setOnInsert: {
      user, sourceType: source.type, sourceValue: source.youtubeUrl || source.file?.originalname || source.artifact?.filename,
      language: source.language || "english", title: source.youtubeUrl || source.file?.originalname || source.artifact?.filename,
      retryOf: source.retryOf, status: "queued", stage: "accepting", percent: 0,
      "retention.expiresAt": new Date(now.getTime() + retentionDays * 86400000),
      "execution.ready": false, "submission.fingerprint": fingerprint,
    } }, { upsert: true, new: true, setDefaultsOnInsert: true });
  } catch (error) {
    if (error.code !== 11000) throw error;
    job = await Job.findById(_id);
  }
  if (!job || String(job.user) !== String(user) || job.submission?.fingerprint !== fingerprint) {
    throw new ApiError(409, "This submission key was already used for different input.", "IDEMPOTENCY_CONFLICT");
  }
  if (["deleting", "deleted"].includes(job.status)) throw new ApiError(410, "This job was deleted.", "JOB_DELETED");
  if (job.execution.ready !== false) return { job, replayed: true };

  const token = randomUUID();
  job = await Job.findOneAndUpdate({ _id, status: "queued", "execution.ready": false,
    $or: [{ "submission.leaseUntil": { $exists: false } }, { "submission.leaseUntil": { $lte: now } }],
  }, { $set: { "submission.token": token, "submission.leaseUntil": new Date(now.getTime() + INTAKE_LEASE_MS) } }, { new: true });
  if (!job) throw new ApiError(409, "This submission is being accepted. Retry with the same key shortly.", "SUBMISSION_IN_PROGRESS");
  const owned = { _id, status: "queued", "execution.ready": false, "submission.token": token, "submission.leaseUntil": { $gt: new Date() } };
  try {
    await usage.reserveJob(user, _id); // Atomic and idempotent, including concurrent retries.
    let artifact = job.sourceArtifact?.fileId ? job.sourceArtifact : undefined;
    if (!artifact && source.artifact?.fileId) artifact = await cloneJobUpload(source.artifact);
    if (!artifact && source.file) artifact = await storeJobUpload({ path: source.file.path,
      filename: source.file.originalname, contentType: source.file.mimetype });
    // Keep unreferenced copies for the existing orphan reconciler on uncertain DB writes.
    const accepted = await Job.findOneAndUpdate({ ...owned, "submission.leaseUntil": { $gt: new Date() } }, {
      $set: { "execution.ready": true, "execution.nextAttemptAt": new Date(), stage: "queued", ...(artifact ? { sourceArtifact: artifact } : {}) },
      $unset: { "submission.token": "", "submission.leaseUntil": "" },
    }, { new: true });
    if (!accepted) throw new ApiError(409, "Submission acceptance is uncertain. Retry with the same key.", "SUBMISSION_IN_PROGRESS");
    return { job: accepted, replayed: false };
  } catch (error) {
    // Do not refund or delete media: the final write may have succeeded despite
    // a network timeout. Replays reconcile the saved intent/result instead.
    await Job.updateOne({ _id, "execution.ready": false, "submission.token": token },
      { $unset: { "submission.token": "", "submission.leaseUntil": "" } }).catch(() => {});
    throw error;
  }
}
