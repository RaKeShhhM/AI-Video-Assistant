import multer from "multer";
import { ApiError } from "./ApiError.js";

const malformedMessages = new Set([
  "Multipart: Boundary not found", "Malformed content type",
  "Unexpected end of form", "Unexpected end of file", "Malformed part header",
]);

// Normalize only at the upload boundary. Never expose parser filenames or
// filesystem errors, and do not misclassify disk failures as client mistakes.
export function uploadError(error) {
  if (error instanceof ApiError) return error;
  if (error instanceof multer.MulterError) {
    return new ApiError(error.code === "LIMIT_FILE_SIZE" ? 413 : 400,
      error.code === "LIMIT_FILE_SIZE" ? "File exceeds the upload size limit."
        : "Invalid multipart upload: check files, fields and their limits.");
  }
  if (malformedMessages.has(error.message)) {
    return new ApiError(400, "Malformed multipart upload.");
  }
  return new ApiError(500, "Could not receive the upload. Please try again.");
}
