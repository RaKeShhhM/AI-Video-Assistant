import { io } from "socket.io-client";
import { API_URL } from "./api";

let socket;

export function connectSocket(token) {
  if (socket) {
    socket.disconnect();
  }
  socket = io(API_URL, { auth: { token }, transports: ["websocket", "polling"], withCredentials: true });
  socket.on("connect_error", (error) => {
    if (/expired|invalid|auth/i.test(error.message || "")) window.dispatchEvent(new Event("auth:expired"));
  });
  return socket;
}

export function getSocket() {
  return socket;
}

export function disconnectSocket() {
  if (socket) {
    socket.disconnect();
    socket = undefined;
  }
}
