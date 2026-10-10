import { ApiError } from "../utils/ApiError.js";

export function notFound(req, res) {
  res.status(404).json({ code: "NOT_FOUND", message: "Route not found." });
}

export function errorHandler(err, req, res, next) {
  if (res.headersSent) return next(err);
  let safe;
  if (err.code === 11000) safe = new ApiError(409, "An account with this email already exists.", "EMAIL_IN_USE");
  else if (err.type === "entity.parse.failed") safe = new ApiError(400, "Malformed JSON body.", "INVALID_JSON");
  else if (err.status === 413) safe = new ApiError(413, "Request body is too large.");
  else if (err instanceof ApiError) safe = err;
  else if (["CastError", "ValidationError"].includes(err.name)) safe = new ApiError(400, "Invalid request data.");
  else safe = new ApiError(500, "Internal server error.");
  if (safe.statusCode === 429) res.set("Retry-After", "60");
  if (safe.statusCode >= 500) console.error("Request failed:", err);
  res.status(safe.statusCode).json({ code: safe.code, message: safe.statusCode === 500 ? "Internal server error." : safe.message });
}
