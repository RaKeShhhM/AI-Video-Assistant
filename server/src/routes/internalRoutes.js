import { Router } from "express";
import { receiveProgress } from "../controllers/videoController.js";
import { requireInternalSecret } from "../middleware/auth.js";

const router = Router();

router.post("/jobs/progress", requireInternalSecret, receiveProgress);

export default router;
