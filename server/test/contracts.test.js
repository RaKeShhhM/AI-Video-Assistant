import assert from "node:assert/strict";
import test from "node:test";
import express from "express";
import { once } from "node:events";
import { authInput, sourceInput, questionInput, progressInput, aiResponse } from "../src/utils/contracts.js";
import { errorHandler } from "../src/middleware/errorHandler.js";
import { register, login } from "../src/controllers/authController.js";
import { getJob, deleteJob, askQuestion, receiveProgress } from "../src/controllers/videoController.js";
import User from "../src/models/User.js";
import Job from "../src/models/Job.js";

const id = "6ab43b980163a134e354a9d9";
const progress = { job_id: id, status: "processing", stage: "transcribing", percent: 20, data: {} };

test("contracts reject malformed types, unknown fields and unsafe bounds", () => {
  for (const value of [null, [], true, 1, "text"]) {
    assert.throws(() => authInput(value));
    assert.throws(() => questionInput(value));
    assert.throws(() => progressInput(value));
  }
  for (const value of [null, {}, [], 42, true, ""]) {
    assert.throws(() => authInput({ email: value, password: "123456" }));
    assert.throws(() => questionInput({ question: value }));
    assert.throws(() => sourceInput({ language: value }, {}));
  }
  assert.deepEqual(authInput({ name: " Sam ", email: " SAM@Example.com ", password: "123456" }, true), { name: "Sam", email: "sam@example.com", password: "123456" });
  for (const password of ["12345", "x".repeat(73), "é".repeat(37)]) assert.throws(() => authInput({ name: "Sam", email: "sam@example.com", password }, true));
  assert.throws(() => authInput({ name: "x".repeat(101), email: "a@b.com", password: "123456" }, true));
  assert.throws(() => authInput({ email: "a@b", password: "123456" }));
  assert.throws(() => questionInput({ question: "x", extra: true }));
  assert.equal(questionInput({ question: " hello " }), "hello");
  for (const patch of [{ job_id: "no" }, { status: "queued" }, { stage: "made_up" }, { percent: "20" }, { percent: -1 }, { percent: 101 }, { percent: 1.5 }, { data: null }, { data: { transcript: {} } }, { data: { duration_seconds: "10" } }, { data: { unknown: "x" } }, { status: "completed", stage: "completed", percent: 90 }]) assert.throws(() => progressInput({ ...progress, ...patch }));
  assert.deepEqual(progressInput(progress), progress);
  assert.throws(() => aiResponse({ answer: {} }), { code: "AI_INVALID_RESPONSE" });
  assert.throws(() => aiResponse({ status: "accepted", job_id: "other" }, id));
  assert.equal(aiResponse({ answer: "OK" }).answer, "OK");
});

test("HTTP boundaries reject before database work and sanitize failures", async t => {
  const app = express();
  app.use(express.json({ limit: "16kb" }));
  app.post("/register", register);
  app.post("/login", login);
  app.get("/jobs/:id", getJob);
  app.delete("/jobs/:id", deleteJob);
  app.post("/jobs/:id/ask", askQuestion);
  app.post("/progress", receiveProgress);
  app.get("/failure", () => { throw new Error("secret C:/private/file provider details"); });
  app.use(errorHandler);
  const server = app.listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(() => new Promise(resolve => { server.close(resolve); server.closeAllConnections(); }));
  const base = `http://127.0.0.1:${server.address().port}`;
  const request = async (path, body, method = "POST") => {
    const response = await fetch(base + path, { method, ...(body !== undefined ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}) });
    return { status: response.status, ...await response.json() };
  };
  t.mock.method(Job, "findOne", () => { throw new Error("must not query"); });
  t.mock.method(Job, "findOneAndDelete", () => { throw new Error("must not delete"); });
  t.mock.method(Job, "findById", () => { throw new Error("must not query"); });
  const lookup = t.mock.method(User, "findOne", async () => null);
  for (const path of ["/register", "/login"]) assert.equal((await request(path, { email: {}, password: [] })).status, 400);
  assert.equal(lookup.mock.callCount(), 0);
  for (const method of ["GET", "DELETE"]) assert.equal((await request("/jobs/invalid", undefined, method)).status, 400);
  assert.equal((await request("/jobs/invalid/ask", { question: "hello" })).status, 400);
  assert.equal((await request("/progress", { ...progress, data: { title: [] } })).status, 400);
  assert.equal(Job.findOne.mock.callCount(), 0);
  assert.equal(Job.findOneAndDelete.mock.callCount(), 0);
  assert.equal(Job.findById.mock.callCount(), 0);
  t.mock.method(User, "hashPassword", async () => "hash");
  t.mock.method(User, "create", async () => { throw Object.assign(new Error("duplicate private db key"), { code: 11000 }); });
  const duplicate = await request("/register", { name: "Sam", email: " SAM@Example.com ", password: "123456" });
  assert.equal(duplicate.status, 409);
  assert.equal(duplicate.code, "EMAIL_IN_USE");
  assert.deepEqual(lookup.mock.calls.at(-1).arguments[0], { email: "sam@example.com" });
  assert.equal(User.create.mock.calls[0].arguments[0].email, "sam@example.com");
  const malformed = await fetch(base + "/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: '{"password":secret}' });
  assert.equal(malformed.status, 400);
  assert.equal((await malformed.json()).code, "INVALID_JSON");
  assert.equal((await request("/login", { email: "x".repeat(17000) })).code, "PAYLOAD_TOO_LARGE");
  const failure = await request("/failure", undefined, "GET");
  assert.equal(failure.status, 500);
  assert.equal(failure.code, "INTERNAL_ERROR");
  assert.doesNotMatch(failure.message, /private|secret|provider/);
});
import axios from "axios";
import { usage } from "../src/services/usageService.js";

test("provider failures never enter chat", async t => {
  t.mock.method(console, "error", () => {});
  const job = { _id: id, user: id, status: "processing", chat: [], save: async () => {} };
  t.mock.method(Job, "findById", async () => job);
  t.mock.method(Job, "findOne", async () => job);
  t.mock.method(Job, "findOneAndUpdate", async (_filter, update) => {
    job.chat.push(update.$push.chat);
    return job;
  });
  t.mock.method(usage, "finishJob", async () => {});
  t.mock.method(usage, "reserveQuestion", async () => {});
  const invoke = async (handler, body) => {
    let response, error;
    await handler({ body, params: { id }, userId: id }, { json: value => { response = value; } }, value => { error = value; });
    return { response, error };
  };
  job.status = "completed";
  const previous = process.env.INTERNAL_AI_SECRET;
  process.env.INTERNAL_AI_SECRET = "test-only-internal-secret-0123456789abcdef";
  t.after(() => { if (previous === undefined) delete process.env.INTERNAL_AI_SECRET; else process.env.INTERNAL_AI_SECRET = previous; });
  const post = t.mock.method(axios, "post", async () => { throw new Error("private provider response"); });
  assert.equal((await invoke(askQuestion, { question: "test" })).error.code, "AI_UNAVAILABLE");
  assert.equal(job.chat.length, 0);
  post.mock.mockImplementation(async () => ({ data: { answer: {} } }));
  assert.equal((await invoke(askQuestion, { question: "test" })).error.code, "AI_INVALID_RESPONSE");
  assert.equal(job.chat.length, 0);
  post.mock.mockImplementation(async () => { throw { response: { status: 409, data: { code: "INDEX_REBUILD_REQUIRED", detail: "private internal path" } } }; });
  const missing = (await invoke(askQuestion, { question: "test" })).error;
  assert.equal(missing.statusCode, 409);
  assert.equal(missing.code, "INDEX_REBUILD_REQUIRED");
  assert.doesNotMatch(missing.message, /private/);
  assert.equal(job.chat.length, 0);
  post.mock.mockImplementation(async () => ({ data: { answer: "Valid answer" } }));
  assert.equal((await invoke(askQuestion, { question: " test " })).response.answer, "Valid answer");
  assert.deepEqual(job.chat, [{ question: "test", answer: "Valid answer" }]);
});
