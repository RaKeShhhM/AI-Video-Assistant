import assert from "node:assert/strict";
import test from "node:test";
import { spawnSync } from "node:child_process";
import axios from "axios";
import { validateInternalSecret, internalSecretMatches } from "../src/config/internalSecurity.js";
import { requireInternalSecret } from "../src/middleware/auth.js";
import { forwardProcessJob, askAiService } from "../src/services/aiService.js";

const SECRET = "test-only-internal-secret-0123456789abcdef";

test("internal secrets reject missing, weak and placeholder configuration", () => {
  for (const value of [null, "", "short", "change_this_shared_secret".repeat(2), "x".repeat(257), "a b".repeat(20)]) {
    assert.throws(() => validateInternalSecret(value), /INTERNAL_AI_SECRET/);
  }
  assert.equal(validateInternalSecret(SECRET), SECRET);
  assert.equal(internalSecretMatches(SECRET, SECRET), true);
  for (const value of [undefined, "", "wrong", [SECRET], "é".repeat(40)]) {
    assert.equal(internalSecretMatches(value, SECRET), false);
  }
});

test("callback middleware fails closed and accepts only the matching header", () => {
  const previous = process.env.INTERNAL_AI_SECRET;
  try {
    for (const configured of ["", SECRET]) {
      process.env.INTERNAL_AI_SECRET = configured;
      for (const provided of [undefined, "wrong", SECRET]) {
        let result;
        requireInternalSecret({ headers: { "x-internal-secret": provided } }, {}, (err) => { result = err; });
        if (!configured) assert.equal(result.statusCode, 503);
        else if (provided !== SECRET) assert.equal(result.statusCode, 401);
        else assert.equal(result, undefined);
      }
    }
  } finally {
    if (previous === undefined) delete process.env.INTERNAL_AI_SECRET;
    else process.env.INTERNAL_AI_SECRET = previous;
  }
});

test("Express refuses startup with an empty secret before connecting to MongoDB", () => {
  const result = spawnSync(process.execPath, ["src/index.js"], {
    cwd: new URL("..", import.meta.url), encoding: "utf8", timeout: 10000,
    env: { ...process.env, INTERNAL_AI_SECRET: "", MONGO_URI: "mongodb://127.0.0.1:1/unused" },
  });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /INTERNAL_AI_SECRET must/);
  assert.doesNotMatch(result.stdout, /MongoDB connected|Server listening/);
});

test("gateway sends header-only credentials and refuses HTTP redirects", async () => {
  const previous = process.env.INTERNAL_AI_SECRET;
  const originalPost = axios.post;
  const calls = [];
  process.env.INTERNAL_AI_SECRET = SECRET;
  axios.post = async (...args) => { calls.push(args); return { data: { answer: "answer" } }; };
  try {
    await forwardProcessJob({ jobId: "6ab43b980163a134e354a9d9", language: "english", youtubeUrl: "https://www.youtube.com/watch?v=BaW_jenozKc" });
    const multipart = calls[0][1].getBuffer().toString();
    assert.doesNotMatch(multipart, /callback_url|callback_secret|service_secret/);
    assert.equal(multipart.includes(SECRET), false);
    assert.equal(await askAiService("6ab43b980163a134e354a9d9", "test"), "answer");
    assert.deepEqual(calls[1][1], { job_id: "6ab43b980163a134e354a9d9", question: "test" });
    for (const [, , options] of calls) {
      assert.equal(options.headers["X-Internal-Secret"], SECRET);
      assert.equal(options.maxRedirects, 0);
    }
  } finally {
    axios.post = originalPost;
    if (previous === undefined) delete process.env.INTERNAL_AI_SECRET;
    else process.env.INTERNAL_AI_SECRET = previous;
  }
});
