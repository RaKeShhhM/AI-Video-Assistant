export function notFound(req, res, next) {
  res.status(404).json({ message: `Route not found: ${req.originalUrl}` });
}

// eslint-disable-next-line no-unused-vars
export function errorHandler(err, req, res, next) {
  if (res.headersSent) return next(err);
  if (err.code?.startsWith("LIMIT_")) {
    return res.status(err.code === "LIMIT_FILE_SIZE" ? 413 : 400).json({
      message: err.code === "LIMIT_FILE_SIZE" ? "File exceeds the upload size limit." : "Invalid multipart upload: too many files/fields or oversized fields.",
    });
  }
  if (err.status === 413) return res.status(413).json({ message: "Request body is too large." });
  const statusCode = err.statusCode || 500;
  if (statusCode === 429) res.set("Retry-After", "60");
  if (statusCode >= 500) {
    console.error(err);
  }
  res.status(statusCode).json({
    message: err.message || "Internal server error",
  });
}
