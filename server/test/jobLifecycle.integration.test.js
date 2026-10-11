import assert from "node:assert/strict";
import test from "node:test";
import { spawn } from "node:child_process";
import { mkdtemp, rm, writeFile, unlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import net from "node:net";
import { once } from "node:events";
import mongoose from "mongoose";
import Job from "../src/models/Job.js";
import ChatMessage from "../src/models/ChatMessage.js";
import ResourceUsage from "../src/models/ResourceUsage.js";
import { usage } from "../src/services/usageService.js";
import { submissionKey, submitJob } from "../src/services/jobSubmission.js";
import { cancelJob, deleteJob, getJob, retryJob, receiveProgress, listJobs, listChat } from "../src/controllers/videoController.js";

const source = { type: "youtube", youtubeUrl: "https://www.youtube.com/watch?v=BaW_jenozKc", language: "english" };
async function invoke(handler, req) {
  let body, error;
  await handler({ headers: {}, ...req }, { json(value) { body = value; }, status() { return this; } }, value => { error = value; });
  if (error) throw error;
  return body;
}

test("submission keys reject malformed headers", () => {
  for (const key of [undefined, "", "short", "a".repeat(129), ["a".repeat(16)], "../".repeat(8)]) assert.throws(() => submissionKey(key));
  assert.equal(submissionKey("valid-key-12345678"), "valid-key-12345678");
});

test("durable submissions and lifecycle fences", { skip: !process.env.S3_MONGOD && !process.env.CALLBACK_TEST_MONGO_URI, timeout: 45000 }, async t => {
  let child, exited, directory;
  let uri = process.env.CALLBACK_TEST_MONGO_URI;
  const database = `r4_test_${new mongoose.Types.ObjectId()}`;
  try {
    if (!uri) {
      directory = await mkdtemp(path.join(tmpdir(), "reel-r4-mongo-"));
      const finder = net.createServer().listen(0, "127.0.0.1");
      await once(finder, "listening");
      const port = finder.address().port;
      await new Promise(resolve => finder.close(resolve));
      child = spawn(process.env.S3_MONGOD, ["--dbpath", directory, "--port", String(port), "--bind_ip", "127.0.0.1", "--quiet"], { windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
      exited = once(child, "exit");
      child.stderr.resume();
      await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error("Mongo startup timed out")), 15000);
        child.once("error", reject);
        child.stdout.on("data", data => { if (data.toString().includes("Waiting for connections")) { clearTimeout(timer); resolve(); } });
      });
      uri = `mongodb://127.0.0.1:${port}`;
    }
    await mongoose.connect(uri, { dbName: database });
    await Promise.all([Job.init(), ResourceUsage.init()]);
    await ChatMessage.init();
    await t.test("R6 paginates jobs and chat with ownership and timestamp tie-breakers", async () => {
      const user = new mongoose.Types.ObjectId();
      const other = new mongoose.Types.ObjectId();
      const timestamp = new Date("2026-01-01T00:00:00.000Z");
      const jobs = await Job.create(Array.from({ length: 3 }, (_, index) => ({
        user, sourceType: "youtube", sourceValue: `https://example.com/${index}`,
        title: `Job ${index}`, status: "completed", createdAt: timestamp, updatedAt: timestamp,
      })));
      await Job.create({ user: other, sourceType: "youtube", sourceValue: "https://example.com/other", status: "completed" });
      await ChatMessage.create(jobs.map((job, index) => ({
        messageId: `r6-message-${index}`, job: jobs[0]._id, user,
        sequence: index + 1, question: `Question ${index}`, answer: `Answer ${index}`,
        createdAt: timestamp, updatedAt: timestamp,
      })));
      const first = await invoke(listJobs, { userId: user, query: { limit: "2" } });
      assert.equal(first.jobs.length, 2);
      assert.ok(first.nextCursor);
      assert.equal("transcript" in first.jobs[0], false);
      assert.equal("chat" in first.jobs[0], false);
      assert.equal("user" in first.jobs[0], false);
      const second = await invoke(listJobs, { userId: user, query: { limit: "2", cursor: first.nextCursor } });
      assert.equal(second.jobs.length, 1);
      assert.notEqual(String(first.jobs[0]._id), String(first.jobs[1]._id));
      const chatFirst = await invoke(listChat, { userId: user, params: { id: String(jobs[0]._id) }, query: { limit: "2" } });
      assert.deepEqual(chatFirst.messages.map(message => message.sequence), [2, 3]);
      const chatSecond = await invoke(listChat, { userId: user, params: { id: String(jobs[0]._id) }, query: { limit: "2", cursor: chatFirst.nextCursor } });
      assert.deepEqual(chatSecond.messages.map(message => message.sequence), [1]);
      assert.equal((await Job.findById(jobs[0]._id)).chat.length, 0);
      await assert.rejects(invoke(listChat, { userId: other, params: { id: String(jobs[0]._id) }, query: {} }), { statusCode: 404 });
    });
    await t.test("concurrent replays create one runnable job and one reservation", async () => {
      const user = new mongoose.Types.ObjectId();
      const results = await Promise.allSettled(Array.from({ length: 12 }, () => submitJob(user, "concurrent-key-1234", source)));
      for (const result of results) if (result.status === "rejected") assert.equal(result.reason.code, "SUBMISSION_IN_PROGRESS");
      const replay = await submitJob(user, "concurrent-key-1234", source);
      assert.equal(replay.replayed, true);
      assert.equal(await Job.countDocuments({ user }), 1);
      assert.equal(replay.job.execution.ready, true);
      const budget = await ResourceUsage.findById(String(user));
      assert.equal(budget.activeJobs.length, 1);
      await assert.rejects(submitJob(user, "concurrent-key-1234", { ...source, language: "hinglish" }), { code: "IDEMPOTENCY_CONFLICT" });
      const other = await submitJob(new mongoose.Types.ObjectId(), "concurrent-key-1234", source);
      assert.notEqual(String(other.job._id), String(replay.job._id));
      await Promise.all(Array.from({ length: 10 }, () => usage.reserveJob(user, replay.job._id)));
      assert.equal((await ResourceUsage.findById(String(user))).secondsReserved, budget.secondsReserved);
    });
    await t.test("uploaded replays reuse one object; retry clones it for independent deletion", async () => {
      const user = new mongoose.Types.ObjectId();
      const filePath = path.join(tmpdir(), `r4-media-${user}.wav`);
      const input = { type: "upload", language: "english", file: { path: filePath, originalname: "sample.wav", mimetype: "audio/wav" } };
      try {
        await writeFile(filePath, "fixture media bytes");
        const first = await submitJob(user, "upload-idempotency-123", input);
        const again = await submitJob(user, "upload-idempotency-123", input);
        assert.equal(String(first.job.sourceArtifact.fileId), String(again.job.sourceArtifact.fileId));
        assert.equal(await mongoose.connection.db.collection("jobMedia.files").countDocuments(), 1);
        await writeFile(filePath, "different media bytes");
        await assert.rejects(submitJob(user, "upload-idempotency-123", input), { code: "IDEMPOTENCY_CONFLICT" });
        await Job.updateOne({ _id: first.job._id }, { $set: { status: "failed" } });
        await usage.finishJob(user, first.job._id, 0);
        const retried = await invoke(retryJob, { userId: user, params: { id: String(first.job._id) }, headers: { "idempotency-key": "retry-upload-key-123" } });
        assert.notEqual(String(retried.job.sourceArtifact.fileId), String(first.job.sourceArtifact.fileId));
        assert.equal(await mongoose.connection.db.collection("jobMedia.files").countDocuments(), 2);
      } finally { await unlink(filePath); }
    });
    await t.test("lost acceptance response is reconciled without a refund or another job", async st => {
      const user = new mongoose.Types.ObjectId();
      const realUpdate = Job.findOneAndUpdate.bind(Job);
      const update = st.mock.method(Job, "findOneAndUpdate", async (...args) => {
        const result = await realUpdate(...args);
        if (args[1].$set?.["execution.ready"] === true) throw new Error("connection lost after commit");
        return result;
      });
      await assert.rejects(submitJob(user, "lost-response-key-123", source));
      update.mock.restore();
      const replay = await submitJob(user, "lost-response-key-123", source);
      assert.equal(replay.replayed, true);
      assert.equal(await Job.countDocuments({ user }), 1);
      assert.equal((await ResourceUsage.findById(String(user))).activeJobs.length, 1);
    });
    await t.test("expired intake can resume after reservation without double charging", async st => {
      const user = new mongoose.Types.ObjectId();
      const realUpdate = Job.findOneAndUpdate.bind(Job);
      const update = st.mock.method(Job, "findOneAndUpdate", async (...args) => {
        if (args[1].$set?.["execution.ready"] === true) throw new Error("crash before acceptance");
        return realUpdate(...args);
      });
      await assert.rejects(submitJob(user, "resume-intake-key-123", source));
      update.mock.restore();
      const before = await ResourceUsage.findById(String(user));
      await Job.updateOne({ user }, { $set: { "submission.token": "crashed-owner", "submission.leaseUntil": new Date(Date.now() + 60000) } });
      await assert.rejects(submitJob(user, "resume-intake-key-123", source), { code: "SUBMISSION_IN_PROGRESS" });
      await Job.updateOne({ user }, { $set: { "submission.leaseUntil": new Date(0) } });
      const resumed = await submitJob(user, "resume-intake-key-123", source);
      assert.equal(resumed.job.execution.ready, true);
      assert.equal((await ResourceUsage.findById(String(user))).secondsReserved, before.secondsReserved);
    });
    await t.test("cancel is terminal, retries are deduplicated successors, old callbacks cannot affect them", async () => {
      const user = new mongoose.Types.ObjectId();
      const { job } = await submitJob(user, "cancel-source-key-123", source);
      const req = { userId: user, params: { id: String(job._id) } };
      const cancelled = (await invoke(cancelJob, req)).job;
      const again = (await invoke(cancelJob, req)).job;
      assert.equal(cancelled.status, "cancelled");
      assert.equal(again.progressVersion, cancelled.progressVersion);
      const retryReq = { ...req, headers: { "idempotency-key": "manual-retry-key-123" } };
      const retry = (await invoke(retryJob, retryReq)).job;
      assert.notEqual(String(retry._id), String(job._id));
      assert.equal(String((await invoke(retryJob, retryReq)).job._id), String(retry._id));
      await invoke(receiveProgress, { body: { job_id: String(job._id), attempt_id: cancelled.execution.attemptId, sequence: cancelled.progressVersion } });
      assert.equal((await ResourceUsage.findById(String(user))).activeJobs.length, 1);
      assert.equal((await Job.findById(retry._id)).status, "queued");
      await assert.rejects(invoke(retryJob, { ...retryReq, params: { id: String(retry._id) } }), { code: "INVALID_JOB_TRANSITION" });
      await assert.rejects(invoke(cancelJob, { ...req, userId: new mongoose.Types.ObjectId() }), { statusCode: 404 });
      await Job.updateOne({ _id: retry._id }, { $set: { status: "completed" } });
      await assert.rejects(invoke(cancelJob, { ...req, params: { id: String(retry._id) } }), { code: "INVALID_JOB_TRANSITION" });
    });
    await t.test("delete fences processing, is owner-scoped, and leaves retryable state", async () => {
      const user = new mongoose.Types.ObjectId();
      const { job } = await submitJob(user, "delete-process-key-123", source);
      const originalAttempt = "a".repeat(32);
      await Job.updateOne({ _id: job._id }, { $set: {
        status: "processing", "execution.attemptId": originalAttempt,
        "execution.leaseUntil": new Date(Date.now() + 60000),
      } });
      const req = { userId: user, params: { id: String(job._id) } };
      await assert.rejects(invoke(deleteJob, { ...req, userId: new mongoose.Types.ObjectId() }), { statusCode: 404 });
      const marked = (await invoke(deleteJob, req)).job;
      assert.equal(marked.status, "deleting");
      assert.equal(marked.deletion.activeAttemptId, originalAttempt);
      assert.equal((await invoke(deleteJob, req)).job.status, "deleting");
      assert.equal((await invoke(receiveProgress, { body: { job_id: String(job._id), attempt_id: originalAttempt, sequence: marked.progressVersion } })).ignored, true);
      assert.equal((await ResourceUsage.findById(String(user))).activeJobs.length, 0);
      assert.equal((await Job.findById(job._id)).execution.ready, false);
      await assert.rejects(submitJob(user, "delete-process-key-123", source), { code: "JOB_DELETED" });
      await Job.updateOne({ _id: job._id }, { $set: { "deletion.state": "failed", "deletion.lastError": "PermissionError" } });
      assert.equal((await invoke(getJob, req)).job.deletion.lastError, "PermissionError");
      assert.equal((await invoke(deleteJob, req)).job.deletion.state, "pending");
      await Job.updateOne({ _id: job._id }, { $set: { status: "deleted" } });
      await assert.rejects(invoke(getJob, req), { statusCode: 404 });
      assert.equal((await invoke(deleteJob, req)).message, "Job deleted");
    });
  } finally {
    if (mongoose.connection.readyState === 1) await mongoose.connection.dropDatabase();
    await mongoose.disconnect();
    if (child) { child.kill(); await exited; }
    if (directory) {
      const target = path.resolve(directory);
      if (path.dirname(target) !== path.resolve(tmpdir()) || !path.basename(target).startsWith("reel-r4-mongo-")) throw new Error("Unsafe cleanup path");
      await rm(target, { recursive: true, force: true });
    }
  }
});
