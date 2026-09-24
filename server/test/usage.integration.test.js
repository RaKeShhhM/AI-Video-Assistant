import assert from "node:assert/strict";
import test from "node:test";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import net from "node:net";
import { once } from "node:events";
import mongoose from "mongoose";
import ResourceUsage from "../src/models/ResourceUsage.js";
import { createUsageService } from "../src/services/usageService.js";

test("MongoDB atomically enforces concurrent admission, daily budgets and idempotent refunds", { skip: !process.env.S3_MONGOD, timeout: 45000 }, async () => {
  const folder = await mkdtemp(path.join(tmpdir(), "reel-s3-mongo-"));
  const portFinder = net.createServer().listen(0, "127.0.0.1");
  await once(portFinder, "listening");
  const port = portFinder.address().port;
  await new Promise((resolve) => portFinder.close(resolve));
  const child = spawn(process.env.S3_MONGOD, ["--dbpath", folder, "--port", String(port), "--bind_ip", "127.0.0.1", "--quiet"], { windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  const exit = once(child, "exit");
  child.stderr.resume();
  let now = new Date("2026-09-25T12:00:00Z");
  const config = { mediaSeconds: 60, dailySeconds: 120, activeJobsPerUser: 1, leaseMs: 7200000, dailyQuestions: 3 };
  try {
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Test MongoDB startup timed out")), 15000);
      child.once("error", reject);
      child.stdout.on("data", (data) => { if (data.toString().includes("Waiting for connections")) { clearTimeout(timer); resolve(); } });
    });
    await mongoose.connect(`mongodb://127.0.0.1:${port}/s3_isolated_test`);
    await ResourceUsage.init();
    const usage = createUsageService(ResourceUsage, config, () => now);
    const results = await Promise.allSettled(Array.from({ length: 20 }, (_, i) => usage.reserveJob("user", `job${i}`)));
    assert.equal(results.filter((result) => result.status === "fulfilled").length, 1);
    let record = await ResourceUsage.findById("user").lean();
    const first = record.activeJobs[0].jobId;
    await Promise.all(Array.from({ length: 10 }, () => usage.finishJob("user", first, 10)));
    record = await ResourceUsage.findById("user").lean();
    assert.equal(record.secondsReserved, 10);
    assert.equal(record.activeJobs.length, 0);
    await usage.reserveJob("user", "second");
    await usage.finishJob("user", "second"); // unknown duration keeps 60 seconds
    await assert.rejects(usage.reserveJob("user", "third"), (err) => err.statusCode === 429);
    const asks = await Promise.allSettled(Array.from({ length: 20 }, () => usage.reserveQuestion("user")));
    assert.equal(asks.filter((result) => result.status === "fulfilled").length, 3);
    await assert.rejects(createUsageService(ResourceUsage, config, () => now).reserveQuestion("user"));
    now = new Date("2026-09-26T12:00:00Z");
    await usage.reserveQuestion("user");
    await usage.reserveJob("user", "nextday");
    record = await ResourceUsage.findById("user").lean();
    assert.equal(record.questions, 1);
    assert.equal(record.secondsReserved, 60);
    await usage.finishJob("user", "second", 0); // old/duplicate cannot refund today's work
    assert.equal((await ResourceUsage.findById("user")).secondsReserved, 60);
  } finally {
    await mongoose.disconnect();
    child.kill();
    await exit;
    const target = path.resolve(folder);
    if (path.dirname(target) !== path.resolve(tmpdir()) || !path.basename(target).startsWith("reel-s3-mongo-")) throw new Error("Unsafe test cleanup path");
    await rm(target, { recursive: true, force: true });
  }
});
