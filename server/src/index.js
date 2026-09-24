import "dotenv/config";
import cors from "cors";
import express from "express";
import http from "http";

import { connectDB } from "./config/db.js";
import { initSocket } from "./config/socket.js";
import { errorHandler, notFound } from "./middleware/errorHandler.js";
import authRoutes from "./routes/authRoutes.js";
import internalRoutes from "./routes/internalRoutes.js";
import videoRoutes from "./routes/videoRoutes.js";
import { validateInternalSecret } from "./config/internalSecurity.js";

// Stop before connecting to MongoDB or opening a listening socket.
validateInternalSecret();

const app = express();

const allowedOrigins = (process.env.CLIENT_ORIGIN || "*").split(",");
app.use(cors({ origin: allowedOrigins, credentials: true }));
app.use(express.json({ limit: "2mb" }));

app.get("/api/health", (req, res) => res.json({ status: "ok" }));
app.use("/api/auth", authRoutes);
app.use("/api/videos", videoRoutes);
app.use("/api/internal", internalRoutes);

app.use(notFound);
app.use(errorHandler);

const PORT = process.env.PORT || 5000;
const server = http.createServer(app);
initSocket(server);

connectDB()
  .then(() => {
    server.listen(PORT, () => console.log(`Server listening on port ${PORT}`));
  })
  .catch((err) => {
    console.error("Failed to connect to MongoDB:", err);
    process.exit(1);
  });
