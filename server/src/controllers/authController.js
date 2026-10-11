import { authInput } from "../utils/contracts.js";
import jwt from "jsonwebtoken";
import { createHash, randomBytes } from "node:crypto";
import User from "../models/User.js";
import RefreshSession from "../models/RefreshSession.js";
import { ApiError } from "../utils/ApiError.js";
import { asyncHandler } from "../utils/asyncHandler.js";

function issueToken(user) {
  return jwt.sign({ id: user._id }, process.env.JWT_SECRET, {
    expiresIn: process.env.JWT_EXPIRES_IN || "15m",
  });
}

const refreshDays = 30;
function hashRefreshToken(token) {
  return createHash("sha256").update(token).digest("hex");
}
async function issueRefreshSession(userId, res) {
  const token = randomBytes(32).toString("base64url");
  const expiresAt = new Date(Date.now() + refreshDays * 86400000);
  await RefreshSession.create({ user: userId, tokenHash: hashRefreshToken(token), expiresAt });
  res.cookie("refresh_token", token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    maxAge: refreshDays * 86400000,
    path: "/api/auth",
  });
}
function clearRefreshCookie(res) {
  res.clearCookie("refresh_token", { httpOnly: true, sameSite: "lax", path: "/api/auth" });
}
async function authenticate(user, res) {
  await issueRefreshSession(user._id, res);
  return { token: issueToken(user), user: user.toSafeJSON() };
}

export const register = asyncHandler(async (req, res) => {
  const { name, email, password } = authInput(req.body, true);

  const existing = await User.findOne({ email });
  if (existing) {
    throw new ApiError(409, "An account with this email already exists.", "EMAIL_IN_USE");
  }

  const passwordHash = await User.hashPassword(password);
  const user = await User.create({ name, email, passwordHash });

  res.status(201).json(await authenticate(user, res));
});

export const login = asyncHandler(async (req, res) => {
  const { email, password } = authInput(req.body);

  const user = await User.findOne({ email });
  if (!user || !(await user.comparePassword(password))) {
    throw new ApiError(401, "Invalid email or password");
  }

  res.json(await authenticate(user, res));
});

export const refresh = asyncHandler(async (req, res) => {
  const token = req.cookies?.refresh_token;
  if (!token) throw new ApiError(401, "Session expired");
  const session = await RefreshSession.findOne({
    tokenHash: hashRefreshToken(token),
    revokedAt: { $exists: false },
    expiresAt: { $gt: new Date() },
  });
  if (!session) {
    clearRefreshCookie(res);
    throw new ApiError(401, "Session expired");
  }
  const user = await User.findById(session.user);
  if (!user) {
    await RefreshSession.updateOne({ _id: session._id }, { $set: { revokedAt: new Date() } });
    clearRefreshCookie(res);
    throw new ApiError(401, "Session expired");
  }
  await RefreshSession.updateOne({ _id: session._id }, { $set: { lastUsedAt: new Date() } });
  res.json({ token: issueToken(user), user: user.toSafeJSON() });
});

export const logout = asyncHandler(async (req, res) => {
  const token = req.cookies?.refresh_token;
  if (token) await RefreshSession.updateOne({ tokenHash: hashRefreshToken(token), revokedAt: { $exists: false } }, { $set: { revokedAt: new Date() } });
  clearRefreshCookie(res);
  res.status(204).end();
});

export const me = asyncHandler(async (req, res) => {
  const user = await User.findById(req.userId);
  if (!user) throw new ApiError(404, "User not found");
  res.json({ user: user.toSafeJSON() });
});
