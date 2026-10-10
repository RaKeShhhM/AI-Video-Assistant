export class ApiError extends Error {
  constructor(statusCode, message, code) {
    super(message);
    this.statusCode = statusCode;
    this.code = code || ({ 400: "INVALID_REQUEST", 401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND", 409: "CONFLICT", 413: "PAYLOAD_TOO_LARGE", 415: "UNSUPPORTED_MEDIA", 422: "INVALID_REQUEST", 429: "RATE_LIMITED", 502: "AI_UNAVAILABLE", 503: "SERVICE_UNAVAILABLE" }[statusCode] || "INTERNAL_ERROR");
  }
}
