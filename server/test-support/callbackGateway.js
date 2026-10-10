// Isolated integration fixture. Never connects using the application's .env.
import express from "express";
import mongoose from "mongoose";
import { receiveProgress } from "../src/controllers/videoController.js";
import { requireInternalSecret } from "../src/middleware/auth.js";
import { errorHandler } from "../src/middleware/errorHandler.js";
await mongoose.connect(process.env.CALLBACK_GATEWAY_TEST_URI);
const app = express();
app.use(express.json());
app.post("/api/internal/jobs/progress", requireInternalSecret, receiveProgress);
app.use(errorHandler);
const server = app.listen(0, "127.0.0.1", () => console.log(server.address().port));
process.stdin.resume();
process.stdin.on("end", async () => {
  server.closeAllConnections();
  server.close();
  await mongoose.disconnect();
});
