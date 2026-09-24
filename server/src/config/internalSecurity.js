import { createHash, timingSafeEqual } from "node:crypto";

export function validateInternalSecret(value = process.env.INTERNAL_AI_SECRET) {
  if (typeof value !== "string" || !/^[!-~]{32,256}$/.test(value)
      || /change_this|changeme|replace_me|your_secret/i.test(value)) {
    throw new Error("INTERNAL_AI_SECRET must be a non-placeholder secret of 32-256 printable ASCII characters without spaces.");
  }
  return value;
}

export function internalSecretMatches(provided, expected) {
  if (typeof provided !== "string" || !provided) return false;
  const digest = (value) => createHash("sha256").update(value).digest();
  return timingSafeEqual(digest(provided), digest(expected));
}
