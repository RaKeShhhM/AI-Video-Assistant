import { ApiError } from "./ApiError.js";
import { limits } from "../config/resourceLimits.js";
import { validateVideoSource } from "./videoSource.js";

function invalid(message) { throw new ApiError(400, message, "INVALID_REQUEST"); }
function object(value, keys) {
  if (!value || typeof value !== "object" || Array.isArray(value)) invalid("Request must be an object.");
  if (Object.keys(value).some(key => !keys.includes(key))) invalid("Request contains unsupported fields.");
  return value;
}
function string(value, field, max, min = 1, trim = true) {
  if (typeof value !== "string" || value.length > max) invalid(`${field} must be a string of ${min}-${max} characters.`);
  const result = trim ? value.trim() : value;
  if (result.length < min) invalid(`${field} must be a string of ${min}-${max} characters.`);
  return result;
}
export function jobId(value) {
  if (typeof value !== "string" || !/^[a-fA-F0-9]{24}$/.test(value)) invalid("Invalid job ID.");
  return value.toLowerCase();
}
export function authInput(body, registration = false) {
  object(body, registration ? ["name", "email", "password"] : ["email", "password"]);
  const email = string(body.email, "email", 254).toLowerCase();
  if (!/^[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+$/.test(email)) invalid("Provide a valid email address.");
  const password = string(body.password, "password", 72, registration ? 6 : 1, false);
  if (Buffer.byteLength(password, "utf8") > 72) invalid("Password must not exceed 72 UTF-8 bytes.");
  return { email, password, ...(registration ? { name: string(body.name, "name", 100) } : {}) };
}
export function sourceInput(body, file) {
  object(body, ["youtubeUrl", "language"]);
  const language = body.language === undefined ? "english" : body.language;
  if (!["english", "hinglish"].includes(language)) invalid("Unsupported language.");
  return { ...validateVideoSource(body, file), language };
}
export function questionInput(body) {
  object(body, ["question"]);
  return string(body.question, "question", limits.questionChars);
}
const stages = ["downloading_audio", "transcribing", "transcript_ready", "generating_title", "summarizing", "summary_ready", "extracting_insights", "insights_ready", "indexing_transcript"];
const textLimits = { title: 300, transcript: 1000000, summary: 100000, action_items: 100000, key_decisions: 100000, open_questions: 100000, error: 2000 };
export function progressInput(body) {
  object(body, ["job_id", "status", "stage", "percent", "data"]);
  const id = jobId(body.job_id);
  if (!["processing", "completed", "failed"].includes(body.status)) invalid("Invalid progress status.");
  const validStage = body.status === "processing" ? stages.includes(body.stage) : body.stage === (body.status === "failed" ? "error" : "completed");
  if (!validStage) invalid("Invalid progress stage for status.");
  if (!Number.isInteger(body.percent) || body.percent < 0 || body.percent > 100 || (body.status === "completed" && body.percent !== 100)) invalid("Invalid progress percentage.");
  const data = object(body.data === undefined ? {} : body.data, [...Object.keys(textLimits), "duration_seconds"]);
  for (const [key, max] of Object.entries(textLimits)) if (Object.hasOwn(data, key)) string(data[key], key, max, 0, false);
  if (Object.hasOwn(data, "duration_seconds") && (!Number.isFinite(data.duration_seconds) || data.duration_seconds <= 0 || data.duration_seconds > limits.mediaSeconds)) invalid("Invalid media duration.");
  return { ...body, job_id: id, data };
}
export function aiResponse(data, expectedJobId) {
  try {
    if (expectedJobId) {
      object(data, ["status", "job_id"]);
      if (data.status !== "accepted" || data.job_id !== expectedJobId) invalid("Invalid acceptance.");
    } else {
      object(data, ["answer"]);
      string(data.answer, "answer", 50000);
    }
  } catch { throw new ApiError(502, "AI service returned an invalid response.", "AI_INVALID_RESPONSE"); }
  return data;
}

// R3 callbacks reference the authoritative MongoDB snapshot, never carry results.
export function callbackInput(body) {
  object(body, ["job_id", "attempt_id", "sequence"]);
  const id = jobId(body.job_id);
  if (typeof body.attempt_id !== "string" || !/^[a-f0-9]{32}$/.test(body.attempt_id)) invalid("Invalid callback attempt ID.");
  if (!Number.isSafeInteger(body.sequence) || body.sequence < 1) invalid("Invalid callback sequence.");
  return { ...body, job_id: id };
}
