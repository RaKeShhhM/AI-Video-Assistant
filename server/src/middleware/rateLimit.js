// Short-window protection for the current single API process. Daily budgets
// are persisted separately in MongoDB, so a restart does not reset them.
export function rateLimit({ max, windowMs, clock = Date.now, key = (req) => req.userId || req.ip }) {
  const buckets = new Map();
  return (req, res, next) => {
    const now = clock();
    if (buckets.size >= 10000) {
      for (const [id, bucket] of buckets) if (bucket.reset <= now) buckets.delete(id);
    }
    const id = String(key(req));
    let bucket = buckets.get(id);
    if (!bucket || bucket.reset <= now) {
      if (!bucket && buckets.size >= 10000) {
        return res.status(429).set("Retry-After", "60").json({ message: "Too many requests. Try again later." });
      }
      bucket = { count: 0, reset: now + windowMs };
      buckets.set(id, bucket);
    }
    if (bucket.count >= max) {
      return res.status(429).set("Retry-After", String(Math.ceil((bucket.reset - now) / 1000)))
        .json({ message: "Too many requests. Try again later." });
    }
    bucket.count++;
    next();
  };
}
