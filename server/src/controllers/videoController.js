import Job from "../models/Job.js";
import { emitJobUpdate } from "../config/socket.js";
import { askAiService, forwardProcessJob } from "../services/aiService.js";
import { ApiError } from "../utils/ApiError.js";
import { asyncHandler } from "../utils/asyncHandler.js";
import { validateVideoSource } from "../utils/videoSource.js";

export const createJob = asyncHandler(async (req, res) => {
  const { language } = req.body || {};
  const { type, youtubeUrl, file } = validateVideoSource(req.body, req.file);

  const job = await Job.create({
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

  // Forward to the AI service after responding — the client already has the
  // job id and will get live updates over Socket.io as the pipeline runs.
  try {
    const callbackUrl = `${process.env.SELF_URL}/api/internal/jobs/progress`;
    await forwardProcessJob({
      jobId: job._id.toString(),
      language: job.language,
      youtubeUrl,
      file,
      callbackUrl,
    });
  } catch (err) {
    job.status = "failed";
    job.stage = "error";
    job.error = "Could not reach the AI processing service. Please try again.";
    await job.save();
    emitJobUpdate(req.userId, job);
    console.error("Failed to forward job to AI service:", err.message);
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
  if (!question || !question.trim()) {
    throw new ApiError(400, "question is required");
  }

  const job = await Job.findOne({ _id: req.params.id, user: req.userId });
  if (!job) throw new ApiError(404, "Job not found");
  if (job.status !== "completed") {
    throw new ApiError(400, "This video is still processing — chat unlocks once it's done");
  }

  const answer = await askAiService(job._id.toString(), question.trim());

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

  await job.save();

  emitJobUpdate(job.user.toString(), job);

  res.json({ received: true });
});
