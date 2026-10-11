import Job from "../models/Job.js";
import ChatMessage from "../models/ChatMessage.js";
import { emitJobUpdate } from "../config/socket.js";
import { askAiService } from "../services/aiService.js";
import { ApiError } from "../utils/ApiError.js";
import { asyncHandler } from "../utils/asyncHandler.js";
import { sourceInput, questionInput, jobId, callbackInput } from "../utils/contracts.js";
import { randomBytes } from "node:crypto";
import { usage } from "../services/usageService.js";
import { cleanupUpload } from "../middleware/upload.js";
import { submissionKey, submitJob } from "../services/jobSubmission.js";
import { decodeCursor, encodeCursor, olderThan, pageLimit } from "../utils/pagination.js";
import { randomUUID } from "node:crypto";

export const createJob = asyncHandler(async (req, res) => {
  req.uploadOwnedByController = true;
  try {
    const key = submissionKey(req.headers?.["idempotency-key"]);
    const source = sourceInput(req.body, req.file);
    if (req.aborted) return;
    const result = await submitJob(req.userId, key, source);
    res.status(result.replayed ? 200 : 201).json(result);
  } finally {
    await cleanupUpload(req).catch(() => console.error("Temporary upload cleanup failed"));
    req.releaseUpload?.();
  }
});

export const listJobs = asyncHandler(async (req, res) => {
  const limit = pageLimit(req.query.limit);
  const cursor = decodeCursor(req.query.cursor);
  const filter = { user: req.userId, status: { $ne: "deleted" }, ...olderThan(cursor) };
  const jobs = await Job.find(filter)
    .sort({ createdAt: -1, _id: -1 })
    .limit(limit + 1)
    .select("_id title sourceType language status stage percent retention deletion progressVersion createdAt updatedAt");
  const hasMore = jobs.length > limit;
  const page = hasMore ? jobs.slice(0, limit) : jobs;
  const last = page[page.length - 1];
  res.json({
    jobs: page,
    nextCursor: hasMore && last ? encodeCursor({ createdAt: last.createdAt, id: last._id }) : null,
  });
});

export const getJob = asyncHandler(async (req, res) => {
  const job = await Job.findOne({ _id: jobId(req.params.id), user: req.userId }).select("-chat -chatSequence");
  if (!job || job.status === "deleted") throw new ApiError(404, "Job not found");
  res.json({ job });
});

async function migrateLegacyChat(job) {
  if (!job.chat?.length) return;
  await ChatMessage.bulkWrite(job.chat.map((message, index) => ({
    updateOne: {
      filter: { messageId: `legacy:${job._id}:${index}` },
      update: { $setOnInsert: {
        messageId: `legacy:${job._id}:${index}`, job: job._id, user: job.user,
        sequence: index - job.chat.length, question: message.question, answer: message.answer,
        createdAt: message.createdAt, updatedAt: message.updatedAt,
      } },
      upsert: true,
    },
  })));
  await Job.updateOne(
    { _id: job._id, user: job.user, chat: { $exists: true, $ne: [] } },
    { $max: { chatSequence: 0 }, $unset: { chat: "" } },
  );
}

export const listChat = asyncHandler(async (req, res) => {
  const limit = pageLimit(req.query.limit, 20, 50);
  const cursor = decodeCursor(req.query.cursor);
  const job = await Job.findOne({ _id: jobId(req.params.id), user: req.userId }).select("user status chat");
  if (!job || job.status === "deleted") throw new ApiError(404, "Job not found");
  await migrateLegacyChat(job);
  const messageCursor = cursor?.sequence !== undefined
    ? { $or: [{ sequence: { $lt: cursor.sequence } }, { sequence: cursor.sequence, _id: { $lt: cursor.id } }] }
    : olderThan(cursor);
  const filter = { job: job._id, user: req.userId, ...messageCursor };
  const messages = await ChatMessage.find(filter).sort({ sequence: -1, _id: -1 }).limit(limit + 1).select("-job -user");
  const hasMore = messages.length > limit;
  const page = (hasMore ? messages.slice(0, limit) : messages).reverse();
  const oldest = page[0];
  res.json({
    messages: page,
    nextCursor: hasMore && oldest ? encodeCursor({ createdAt: oldest.createdAt, id: oldest._id, sequence: oldest.sequence }) : null,
  });
});

export const deleteJob = asyncHandler(async (req, res) => {
  const _id = jobId(req.params.id);
  const now = new Date();
  const invalidated = randomBytes(16).toString("hex");
  let job = await Job.findOneAndUpdate({ _id, user: req.userId,
    status: { $nin: ["deleting", "deleted"] },
  }, [{ $set: {
    status: "deleting", stage: "deleting", "execution.ready": false,
    "deletion.state": "pending", "deletion.requestedAt": now, "deletion.nextAt": now,
    "deletion.activeAttemptId": "$execution.attemptId",
    "deletion.workerStoppedAt": { $cond: [{ $eq: ["$status", "processing"] }, null, now] },
    "deletion.waitUntil": { $ifNull: ["$execution.leaseUntil", now] },
    "deletion.attempts": 0, "deletion.lastError": "",
    "execution.attemptId": invalidated, "execution.leaseUntil": null,
    "submission.token": null, "submission.leaseUntil": null,
    progressVersion: { $add: [{ $ifNull: ["$progressVersion", 0] }, 1] },
  } }, { $unset: ["delivery"] }], { new: true });
  if (!job) {
    job = await Job.findOne({ _id, user: req.userId });
    if (!job) throw new ApiError(404, "Job not found");
    if (job.status === "deleted") return res.json({ message: "Job deleted" });
    if (job.deletion?.state === "failed") {
      job = await Job.findOneAndUpdate({ _id, user: req.userId, status: "deleting", "deletion.state": "failed" },
        { $set: { "deletion.state": "pending", "deletion.nextAt": now, "deletion.attempts": 0, "deletion.lastError": "" } }, { new: true }) || job;
    }
  }
  // The tombstone remains authoritative if quota reconciliation times out.
  await usage.finishJob(req.userId, _id, job.durationSeconds);
  emitJobUpdate(String(req.userId), job);
  res.status(202).json({ job });
});

// Terminal jobs stay immutable. A manual retry creates a separately budgeted
// successor, so an old callback can never refund or overwrite the new run.
export const retryJob = asyncHandler(async (req, res) => {
  const key = submissionKey(req.headers?.["idempotency-key"]);
  const previous = await Job.findOne({ _id: jobId(req.params.id), user: req.userId });
  if (!previous) throw new ApiError(404, "Job not found");
  if (!["failed", "cancelled"].includes(previous.status)) {
    throw new ApiError(409, "Only failed or cancelled jobs can be retried.", "INVALID_JOB_TRANSITION");
  }
  if (previous.sourceType === "upload" && !previous.sourceArtifact?.fileId) {
    throw new ApiError(409, "The original upload is unavailable. Upload the file again.", "SOURCE_UNAVAILABLE");
  }
  await usage.finishJob(previous.user, previous._id, previous.durationSeconds);
  const result = await submitJob(req.userId, key, {
    type: previous.sourceType, youtubeUrl: previous.sourceType === "youtube" ? previous.sourceValue : undefined,
    artifact: previous.sourceArtifact, language: previous.language, retryOf: previous._id,
  });
  res.status(result.replayed ? 200 : 201).json(result);
});

export const cancelJob = asyncHandler(async (req, res) => {
  const _id = jobId(req.params.id);
  const now = new Date();
  let job = await Job.findOneAndUpdate({ _id, user: req.userId,
    status: { $in: ["queued", "processing"] }, "execution.ready": { $ne: false },
  }, { $set: {
    status: "cancelled", stage: "cancelled", error: "Processing was cancelled.",
    "execution.checkpoint": "cancelled", "execution.attemptId": randomBytes(16).toString("hex"),
    delivery: { state: "pending", attempts: 0, nextAt: now },
  }, $inc: { progressVersion: 1 }, $unset: { "execution.leaseUntil": "", "execution.workerId": "" } }, { new: true });
  if (!job) {
    job = await Job.findOne({ _id, user: req.userId });
    if (!job) throw new ApiError(404, "Job not found");
    if (job.status !== "cancelled") throw new ApiError(409, "This job can no longer be cancelled.", "INVALID_JOB_TRANSITION");
  }
  // The durable notification retries quota reconciliation if this request fails.
  await usage.finishJob(job.user, job._id, job.durationSeconds);
  emitJobUpdate(String(job.user), job);
  res.json({ job });
});

export const askQuestion = asyncHandler(async (req, res) => {
  const question = questionInput(req.body);

  const job = await Job.findOne({ _id: jobId(req.params.id), user: req.userId });
  if (!job) throw new ApiError(404, "Job not found");
  if (job.status !== "completed") {
    throw new ApiError(400, "This video is still processing — chat unlocks once it's done");
  }
  await migrateLegacyChat(job);

  await usage.reserveQuestion(req.userId);
  let answer;
  try {
    answer = await askAiService(job._id.toString(), question.trim(), job.indexAttempt);
  } catch (err) {
    if (err.response?.status === 409 && err.response.data?.code === "INDEX_REBUILD_REQUIRED") {
      throw new ApiError(409, "This video's search index is unavailable. Submit the video again to rebuild it.", "INDEX_REBUILD_REQUIRED");
    }
    if (err.response?.status === 429) throw new ApiError(429, "AI chat capacity is full. Try again later.");
    if (err instanceof ApiError) throw err;
    throw new ApiError(502, "AI chat is temporarily unavailable. Please try again.");
  }

  const counter = await Job.findOneAndUpdate(
    { _id: job._id, user: req.userId, status: "completed" },
    { $inc: { chatSequence: 1 } },
    { new: true, select: "chatSequence" }
  );
  if (!counter) throw new ApiError(409, "This job was deleted while the answer was being prepared.", "JOB_DELETING");
  const message = await ChatMessage.create({
    messageId: randomUUID(),
    job: job._id,
    user: req.userId,
    sequence: counter.chatSequence,
    question: question.trim(),
    answer,
  });
  res.json({ answer, message });
});

// The worker atomically persists the result and pending notification in MongoDB.
// HTTP callbacks reconcile side effects; they never overwrite worker-owned state.
export const receiveProgress = asyncHandler(async (req, res) => {
  const event = callbackInput(req.body);
  const job = await Job.findById(event.job_id);
  if (!job) throw new ApiError(404, "Job not found");
  const ack = { received: true, attempt_id: event.attempt_id, sequence: event.sequence };
  if (job.execution?.attemptId !== event.attempt_id || job.progressVersion !== event.sequence) {
    return res.json({ ...ack, ignored: true });
  }
  if ((job.callbackAck || 0) >= event.sequence) return res.json(ack);
  // Idempotent reservation removal occurs before ack. If the gateway crashes
  // here, retry safely repeats it; no lost refund between a DB ack and side effect.
  if (["completed", "failed", "cancelled"].includes(job.status)) {
    await usage.finishJob(job.user, job._id, job.durationSeconds);
  }
  const current = await Job.findOneAndUpdate({
    _id: job._id, "execution.attemptId": event.attempt_id, progressVersion: event.sequence,
    $or: [{ callbackAck: { $lt: event.sequence } }, { callbackAck: { $exists: false } }],
  }, { $set: { callbackAck: event.sequence } }, { new: true });
  if (current) emitJobUpdate(current.user.toString(), current);
  res.json(ack);
});
