import { Router } from "express";
import multer from "multer";
import {
  askQuestion,
  createJob,
  deleteJob,
  getJob,
  listJobs,
} from "../controllers/videoController.js";
import { requireAuth } from "../middleware/auth.js";

const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 500 * 1024 * 1024 }, // 500MB cap on uploaded video/audio files
});

const router = Router();

router.use(requireAuth);

router.post("/", upload.single("file"), createJob);
router.get("/", listJobs);
router.get("/:id", getJob);
router.delete("/:id", deleteJob);
router.post("/:id/ask", askQuestion);

export default router;
