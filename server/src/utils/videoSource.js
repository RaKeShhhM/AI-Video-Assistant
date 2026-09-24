import { ApiError } from "./ApiError.js";

const HOSTS = new Set(["youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"]);
const VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;
const INVALID_URL = "Provide an HTTPS YouTube video URL (watch, youtu.be, shorts, embed, or live).";

export function normalizeYouTubeUrl(value) {
  if (typeof value !== "string" || value.length > 2048) throw new ApiError(400, INVALID_URL);
  const input = value.trim();
  if (!/^https:\/\//i.test(input) || /[\s\\\x00-\x1f\x7f]/.test(input)) {
    throw new ApiError(400, INVALID_URL);
  }
  let url;
  try { url = new URL(input); } catch { throw new ApiError(400, INVALID_URL); }
  const authority = input.slice(8).split(/[/?#]/, 1)[0].toLowerCase();
  // Checking the raw authority also rejects encoded hosts, userinfo and odd ports.
  if (!HOSTS.has(authority.replace(/:443$/, "")) || !HOSTS.has(url.hostname)) {
    throw new ApiError(400, INVALID_URL);
  }
  const rawPath = input.slice(8 + authority.length).split(/[?#]/, 1)[0];
  if (rawPath.includes("%") || rawPath.includes("..")) throw new ApiError(400, INVALID_URL);
  if ([...url.searchParams.keys()].some((key) => key.toLowerCase() === "list") || url.pathname === "/playlist") {
    throw new ApiError(400, "Playlists are not supported. Submit a single video without the list parameter.");
  }
  let id;
  if (url.hostname === "youtu.be") {
    id = /^\/([A-Za-z0-9_-]{11})\/?$/.exec(url.pathname)?.[1];
  } else if (url.pathname === "/watch") {
    const ids = url.searchParams.getAll("v");
    if (ids.length === 1) id = ids[0];
  } else {
    id = /^\/(?:shorts|embed|live)\/([A-Za-z0-9_-]{11})\/?$/.exec(url.pathname)?.[1];
  }
  if (!id || !VIDEO_ID.test(id)) throw new ApiError(400, INVALID_URL);
  return `https://www.youtube.com/watch?v=${id}`;
}

export function validateVideoSource(body = {}, file) {
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    throw new ApiError(400, "Provide exactly one source: youtubeUrl or a file upload.");
  }
  const hasUrl = Object.prototype.hasOwnProperty.call(body, "youtubeUrl");
  if (hasUrl === Boolean(file)) {
    throw new ApiError(400, "Provide exactly one source: youtubeUrl or a file upload.");
  }
  return hasUrl
    ? { type: "youtube", youtubeUrl: normalizeYouTubeUrl(body.youtubeUrl) }
    : { type: "upload", file };
}
