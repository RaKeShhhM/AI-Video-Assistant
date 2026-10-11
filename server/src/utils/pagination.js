import mongoose from "mongoose";
import { ApiError } from "./ApiError.js";

export function pageLimit(value, fallback = 10, maximum = 50) {
  if (value === undefined) return fallback;
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < 1 || parsed > maximum) {
    throw new ApiError(400, `limit must be an integer from 1 to ${maximum}.`, "INVALID_REQUEST");
  }
  return parsed;
}

export function encodeCursor(value) {
  return Buffer.from(JSON.stringify(value), "utf8").toString("base64url");
}

export function decodeCursor(value) {
  if (value === undefined) return null;
  if (typeof value !== "string" || value.length > 512) {
    throw new ApiError(400, "Invalid cursor.", "INVALID_REQUEST");
  }
  try {
    const decoded = JSON.parse(Buffer.from(value, "base64url").toString("utf8"));
    if (!decoded.createdAt || !mongoose.isValidObjectId(decoded.id)) throw new Error("invalid cursor");
    const createdAt = new Date(decoded.createdAt);
    if (Number.isNaN(createdAt.getTime())) throw new Error("invalid cursor");
    return { createdAt, id: new mongoose.Types.ObjectId(decoded.id), sequence: decoded.sequence };
  } catch {
    throw new ApiError(400, "Invalid cursor.", "INVALID_REQUEST");
  }
}

export function olderThan(cursor) {
  if (!cursor) return {};
  return {
    $or: [
      { createdAt: { $lt: cursor.createdAt } },
      { createdAt: cursor.createdAt, _id: { $lt: cursor.id } },
    ],
  };
}
