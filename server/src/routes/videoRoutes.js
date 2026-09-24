import { Router } from "express";
import { uploadMedia } from "../middleware/upload.js";
import { rateLimit } from "../middleware/rateLimit.js";
import { limits, mediaExtensions } from "../config/resourceLimits.js";
import {
  askQuestion,
  createJob,
  deleteJob,
  getJob,
  listJobs,
} from "../controllers/videoController.js";
import { requireAuth } from "../middleware/auth.js";

const router = Router();

router.use(requireAuth);

router.get("/limits", (req, res) => res.json({ maxUploadBytes: limits.uploadBytes, maxMediaSeconds: limits.mediaSeconds,
  extensions: mediaExtensions, dailyMediaMinutes: limits.dailySeconds / 60, dailyQuestions: limits.dailyQuestions }));
router.post("/", rateLimit({ max: 10, windowMs: 3600000 }), uploadMedia, createJob);
router.get("/", listJobs);
router.get("/:id", getJob);
router.delete("/:id", deleteJob);
router.post("/:id/ask", rateLimit({ max: 20, windowMs: 60000 }), askQuestion);

export default router;
