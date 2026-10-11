import { Router } from "express";
import { login, logout, me, refresh, register } from "../controllers/authController.js";
import { requireAuth } from "../middleware/auth.js";
import { rateLimit } from "../middleware/rateLimit.js";

const router = Router();

router.post("/register", rateLimit({ max: 5, windowMs: 3600000 }), register);
router.post("/login", rateLimit({ max: 20, windowMs: 900000 }), login);
router.get("/me", requireAuth, me);
router.post("/refresh", refresh);
router.post("/logout", logout);

export default router;
