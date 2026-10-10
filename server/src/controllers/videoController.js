import Job from "../models/Job.js";
import { emitJobUpdate } from "../config/socket.js";
import { askAiService } from "../services/aiService.js";
import { ApiError } from "../utils/ApiError.js";
import { asyncHandler } from "../utils/asyncHandler.js";
import { sourceInput, questionInput, jobId, callbackInput } from "../utils/contracts.js";
import mongoose from "mongoose";
import { usage } from "../services/usageService.js";
import { cleanupUpload } from "../middleware/upload.js";
import { deleteJobUpload, storeJobUpload } from "../services/jobMedia.js";

export const createJob = asyncHandler(async (req, res) => {
  req.uploadOwnedByController = true;
  let job;
  let reserved = false;
  let media;
  const jobId = new mongoose.Types.ObjectId();
  try {
    const { type, youtubeUrl, file, language } = sourceInput(req.body, req.file);
    if (req.aborted) return;
    await usage.reserveJob(req.userId, jobId);
    reserved = true;

    if (file) media = await storeJobUpload({
      path: file.path,
      filename: file.originalname,
      contentType: file.mimetype,
    });

    job = await Job.create({
      _id: jobId,
      user: req.userId,
      sourceType: type,
      sourceValue: youtubeUrl || file.originalname,
      language: language || "english",
      status: "queued",
      stage: "queued",
      percent: 0,
      title: youtubeUrl ? youtubeUrl : file.originalname,
      sourceArtifact: media,
    });

    res.status(201).json({ job });
  } catch (err) {
    if (media && !job) await deleteJobUpload(media.fileId).catch(() => {});
    if (reserved && !job) await usage.finishJob(req.userId, jobId, 0);
    throw err;
  } finally {
    await cleanupUpload(req).catch(() => console.error("Temporary upload cleanup failed"));
    req.releaseUpload?.();
  }
});

export const listJobs = asyncHandler(async (req, res) => {
  const jobs = await Job.find({ user: req.userId })
    .sort({ createdAt: -1 })
    .select("-transcript -chat");
  res.json({ jobs });
});

export const getJob = asyncHandler(async (req, res) => {
  const job = await Job.findOne({ _id: jobId(req.params.id), user: req.userId });
  if (!job) throw new ApiError(404, "Job not found");
  res.json({ job });
});

export const deleteJob = asyncHandler(async (req, res) => {
  const job = await Job.findOneAndDelete({ _id: jobId(req.params.id), user: req.userId });
  if (!job) throw new ApiError(404, "Job not found");
  res.json({ message: "Job deleted" });
});

export const askQuestion = asyncHandler(async (req, res) => {
  const question = questionInput(req.body);

  const job = await Job.findOne({ _id: jobId(req.params.id), user: req.userId });
  if (!job) throw new ApiError(404, "Job not found");
  if (job.status !== "completed") {
    throw new ApiError(400, "This video is still processing — chat unlocks once it's done");
  }

  await usage.reserveQuestion(req.userId);
  let answer;
  try {
    answer = await askAiService(job._id.toString(), question.trim());
  } catch (err) {
    if (err.response?.status === 409 && err.response.data?.code === "INDEX_REBUILD_REQUIRED") {
      throw new ApiError(409, "This video's search index is unavailable. Submit the video again to rebuild it.", "INDEX_REBUILD_REQUIRED");
    }
    if (err.response?.status === 429) throw new ApiError(429, "AI chat capacity is full. Try again later.");
    if (err instanceof ApiError) throw err;
    throw new ApiError(502, "AI chat is temporarily unavailable. Please try again.");
  }

  job.chat.push({ question: question.trim(), answer });
  await job.save();

  res.json({ answer, chat: job.chat });
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
  if (["completed", "failed"].includes(job.status)) {
    await usage.finishJob(job.user, job._id, job.durationSeconds);
  }
  const current = await Job.findOneAndUpdate({
    _id: job._id, "execution.attemptId": event.attempt_id, progressVersion: event.sequence,
    $or: [{ callbackAck: { $lt: event.sequence } }, { callbackAck: { $exists: false } }],
  }, { $set: { callbackAck: event.sequence } }, { new: true });
  if (current) emitJobUpdate(current.user.toString(), current);
  res.json(ack);
});
