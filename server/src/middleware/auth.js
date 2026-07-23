import jwt from "jsonwebtoken";
import { ApiError } from "../utils/ApiError.js";

export function requireAuth(req, res, next) {
  const header = req.headers.authorization || "";
  const token = header.startsWith("Bearer ") ? header.slice(7) : null;

  if (!token) {
    return next(new ApiError(401, "Not authenticated"));
  }

  try {
    const decoded = jwt.verify(token, process.env.JWT_SECRET);
    req.userId = decoded.id;
    next();
  } catch (err) {
    next(new ApiError(401, "Invalid or expired token"));
  }
}

export function requireInternalSecret(req, res, next) {
  const secret = req.headers["x-internal-secret"];
  if (!secret || secret !== process.env.INTERNAL_AI_SECRET) {
    return next(new ApiError(401, "Invalid internal secret"));
  }
  next();
}
