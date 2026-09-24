import Job from "../models/Job.js";
import { emitJobUpdate } from "../config/socket.js";
import { askAiService, forwardProcessJob } from "../services/aiService.js";
import { ApiError } from "../utils/ApiError.js";
import { asyncHandler } from "../utils/asyncHandler.js";
import { validateVideoSource } from "../utils/videoSource.js";
import mongoose from "mongoose";
import { usage } from "../services/usageService.js";
import { cleanupUpload } from "../middleware/upload.js";
import { limits } from "../config/resourceLimits.js";

export const createJob = asyncHandler(async (req, res) => {
  req.uploadOwnedByController = true;
  let job;
  let reserved = false;
  const jobId = new mongoose.Types.ObjectId();
  try {
    const { language } = req.body || {};
    const { type, youtubeUrl, file } = validateVideoSource(req.body, req.file);
    if (language && !["english", "hinglish"].includes(language)) throw new ApiError(400, "Unsupported language.");
    if (req.aborted) return;
    await usage.reserveJob(req.userId, jobId);
    reserved = true;

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
    });

    res.status(201).json({ job });

    // Keep disk storage until forwarding finishes; never remove it merely
    // because the early 201 response has been delivered to the browser.
    try {
      await forwardProcessJob({
        jobId: job._id.toString(),
        language: job.language,
        youtubeUrl,
        file,
      });
    } catch (err) {
      job.status = "failed";
      job.stage = "error";
      const rejected = [400, 413, 415, 422, 429].includes(err.response?.status);
      job.error = rejected && typeof err.response.data?.detail === "string"
        ? err.response.data.detail : "Could not reach the AI processing service. Please try again.";
      await job.save();
      await usage.finishJob(req.userId, job._id, rejected ? 0 : undefined);
      emitJobUpdate(req.userId, job);
      console.error("Failed to forward job to AI service:", err.message);
    }
  } catch (err) {
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
  const job = await Job.findOne({ _id: req.params.id, user: req.userId });
  if (!job) throw new ApiError(404, "Job not found");
  res.json({ job });
});

export const deleteJob = asyncHandler(async (req, res) => {
  const job = await Job.findOneAndDelete({ _id: req.params.id, user: req.userId });
  if (!job) throw new ApiError(404, "Job not found");
  res.json({ message: "Job deleted" });
});

export const askQuestion = asyncHandler(async (req, res) => {
  const { question } = req.body;
  if (typeof question !== "string" || !question.trim() || question.length > limits.questionChars) {
    throw new ApiError(400, `question must contain 1-${limits.questionChars} characters`);
  }

  const job = await Job.findOne({ _id: req.params.id, user: req.userId });
  if (!job) throw new ApiError(404, "Job not found");
  if (job.status !== "completed") {
    throw new ApiError(400, "This video is still processing — chat unlocks once it's done");
  }

  await usage.reserveQuestion(req.userId);
  let answer;
  try {
    answer = await askAiService(job._id.toString(), question.trim());
  } catch (err) {
    if (err.response?.status === 429) throw new ApiError(429, "AI chat capacity is full. Try again later.");
    throw err;
  }

  job.chat.push({ question: question.trim(), answer });
  await job.save();

  res.json({ answer, chat: job.chat });
});

// Called by the AI service (internal, secret-protected) after each pipeline stage.
export const receiveProgress = asyncHandler(async (req, res) => {
  const { job_id: jobId, status, stage, percent, data } = req.body;

  const job = await Job.findById(jobId);
  if (!job) {
    return res.status(404).json({ message: "Job not found" });
  }

  job.status = status || job.status;
  job.stage = stage || job.stage;
  job.percent = typeof percent === "number" ? percent : job.percent;

  if (data?.title) job.title = data.title;
  if (data?.transcript) job.transcript = data.transcript;
  if (data?.summary) job.summary = data.summary;
  if (data?.action_items) job.actionItems = data.action_items;
  if (data?.key_decisions) job.keyDecisions = data.key_decisions;
  if (data?.open_questions) job.openQuestions = data.open_questions;
  if (data?.error) job.error = data.error;
  if (Number.isFinite(data?.duration_seconds) && data.duration_seconds > 0 && data.duration_seconds <= limits.mediaSeconds) {
    job.durationSeconds = data.duration_seconds;
  }

  await job.save();
  if (["completed", "failed"].includes(job.status)) await usage.finishJob(job.user, job._id, job.durationSeconds);

  emitJobUpdate(job.user.toString(), job);

  res.json({ received: true });
});
