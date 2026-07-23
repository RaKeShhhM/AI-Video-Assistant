import jwt from "jsonwebtoken";
import { Server } from "socket.io";

let io;

export function initSocket(httpServer) {
  const origins = (process.env.CLIENT_ORIGIN || "*").split(",");

  io = new Server(httpServer, {
    cors: { origin: origins, credentials: true },
  });

  io.use((socket, next) => {
    try {
      const token = socket.handshake.auth?.token;
      if (!token) return next(new Error("No auth token provided"));
      const decoded = jwt.verify(token, process.env.JWT_SECRET);
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
  io.to(`user:${userId}`).emit("job:update", job);
}
