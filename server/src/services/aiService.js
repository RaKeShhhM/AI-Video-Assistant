import axios from "axios";
import FormData from "form-data";
import { validateInternalSecret } from "../config/internalSecurity.js";

const AI_SERVICE_URL = process.env.AI_SERVICE_URL || "http://localhost:8000";

/**
 * Kicks off a processing job on the AI service. Fire-and-forget from the
 * caller's perspective — the AI service responds as soon as it has accepted
 * the job and runs the actual pipeline in a background task, reporting
 * progress via callbackUrl.
 */
export async function forwardProcessJob({ jobId, language, youtubeUrl, file }) {
  const form = new FormData();
  form.append("job_id", jobId);
  form.append("language", language);

  if (youtubeUrl) {
    form.append("youtube_url", youtubeUrl);
  } else if (file) {
    form.append("file", file.buffer, { filename: file.originalname });
  }

  await axios.post(`${AI_SERVICE_URL}/process`, form, {
    headers: { ...form.getHeaders(), "X-Internal-Secret": validateInternalSecret() },
    maxRedirects: 0,
    maxBodyLength: Infinity,
    maxContentLength: Infinity,
    timeout: 30000,
  });
}

export async function askAiService(jobId, question) {
  const { data } = await axios.post(
    `${AI_SERVICE_URL}/ask`,
    { job_id: jobId, question },
    { timeout: 60000, maxRedirects: 0, headers: { "X-Internal-Secret": validateInternalSecret() } }
  );
  return data.answer;
}
