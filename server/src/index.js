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
const proxyHops = Number(process.env.TRUST_PROXY_HOPS || 0);
if (!Number.isInteger(proxyHops) || proxyHops < 0 || proxyHops > 5) throw new Error("Invalid TRUST_PROXY_HOPS");
app.set("trust proxy", proxyHops);

app.get("/api/health", (req, res) => res.json({ status: "ok" }));
app.use("/api/auth", express.json({ limit: "16kb" }), authRoutes);
app.use("/api/videos", express.json({ limit: "16kb" }), videoRoutes);
app.use("/api/internal", express.json({ limit: "2mb" }), internalRoutes);

app.use(notFound);
app.use(errorHandler);

const PORT = process.env.PORT || 5000;
const server = http.createServer(app);
server.requestTimeout = 120000;
server.headersTimeout = 15000;
initSocket(server);

connectDB()
  .then(() => {
    server.listen(PORT, () => console.log(`Server listening on port ${PORT}`));
  })
  .catch((err) => {
    console.error("Failed to connect to MongoDB:", err);
    process.exit(1);
  });
