import { randomUUID } from "node:crypto";

export function requestLogger(req, res, next) {
  const requestId = req.get("X-Request-ID")?.trim() || randomUUID();
  req.requestId = requestId;
  res.set("X-Request-ID", requestId);
  const started = Date.now();
  res.on("finish", () => {
    console.log(JSON.stringify({
      event: "http_request",
      request_id: requestId,
      method: req.method,
      path: req.path,
      status: res.statusCode,
      duration_ms: Date.now() - started,
      user_id: req.userId,
    }));
  });
  next();
}
