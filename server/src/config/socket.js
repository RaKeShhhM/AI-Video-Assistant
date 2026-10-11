import jwt from "jsonwebtoken";
import { Server } from "socket.io";
import User from "../models/User.js";

let io;

export function initSocket(httpServer) {
  const origins = (process.env.CLIENT_ORIGIN || "*").split(",");

  io = new Server(httpServer, {
    cors: { origin: origins, credentials: true },
  });

  io.use(async (socket, next) => {
    try {
      const token = socket.handshake.auth?.token;//taking the token from the handshake auth object
      if (!token) return next(new Error("No auth token provided"));
      const decoded = jwt.verify(token, process.env.JWT_SECRET);
      if (typeof decoded.id !== "string" || !await User.exists({ _id: decoded.id })) {
        return next(new Error("Account is unavailable"));
      }
      socket.userId = decoded.id;
      next();
    } catch (err) {
      next(new Error("Invalid or expired token"));
    }
  });

  io.on("connection", (socket) => {
    const room = `user:${socket.userId}`;
    socket.join(room);
    socket.on("disconnect", () => {
      // no-op: room membership is cleaned up automatically
    });
  });

  return io;
}

export function getIO() {
  if (!io) throw new Error("Socket.io not initialized yet");
  return io;
}

export function emitJobUpdate(userId, job) {
  if (!io) return;
  const source = job?.toJSON ? job.toJSON() : job;
  const compact = {};
  for (const field of ["_id", "status", "stage", "percent", "title", "sourceType",
    "language", "error", "durationSeconds", "progressVersion", "retention", "deletion",
    "createdAt", "updatedAt"]) {
    if (source?.[field] !== undefined) compact[field] = source[field];
  }
  io.to(`user:${userId}`).emit("job:update", { version: 1, job: compact });
}
