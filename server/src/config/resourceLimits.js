function positive(name, fallback, maximum) {
  const value = Number(process.env[name] ?? fallback);
  if (!Number.isSafeInteger(value) || value < 1 || value > maximum) throw new Error(`Invalid ${name}`);
  return value;
}

export const limits = Object.freeze({
  uploadBytes: positive("MAX_UPLOAD_MB", 100, 500) * 1024 * 1024,
  mediaSeconds: positive("MAX_MEDIA_SECONDS", 1800, 7200),
  activeJobsPerUser: positive("MAX_ACTIVE_JOBS_PER_USER", 1, 4),
  uploadSlots: positive("MAX_CONCURRENT_UPLOADS", 3, 16),
  dailySeconds: positive("DAILY_MEDIA_MINUTES", 120, 1440) * 60,
  dailyQuestions: positive("DAILY_QUESTIONS", 100, 10000),
  questionChars: 2000,
  leaseMs: 2 * 60 * 60 * 1000,
});
if (limits.dailySeconds < limits.mediaSeconds) throw new Error("DAILY_MEDIA_MINUTES must cover at least one MAX_MEDIA_SECONDS reservation");
export const mediaExtensions = [".mp4", ".m4a", ".mov", ".mkv", ".webm", ".mp3", ".wav", ".ogg", ".flac", ".aac"];
