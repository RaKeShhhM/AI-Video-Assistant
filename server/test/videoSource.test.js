import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { normalizeYouTubeUrl, validateVideoSource } from "../src/utils/videoSource.js";
import { createJob } from "../src/controllers/videoController.js";
import Job from "../src/models/Job.js";

const cases = JSON.parse(readFileSync(new URL("../../tests/fixtures/video_sources.json", import.meta.url)));

test("supported URLs normalize to the same single-video address", () => {
  for (const value of cases.valid) assert.equal(normalizeYouTubeUrl(value), cases.canonical);
});

test("unsafe, ambiguous and unsupported URLs are rejected", () => {
  for (const value of [...cases.invalid, "x".repeat(2049)]) {
    assert.throws(() => normalizeYouTubeUrl(value), (err) => err.statusCode === 400, JSON.stringify(value));
  }
});

test("exactly one source is required, including empty URL fields", () => {
  const file = { originalname: "meeting.wav" };
  for (const [body, upload] of [[{}, undefined], [{ youtubeUrl: cases.canonical }, file], [{ youtubeUrl: "" }, file]]) {
    assert.throws(() => validateVideoSource(body, upload), (err) => err.statusCode === 400);
  }
  assert.deepEqual(validateVideoSource({}, file), { type: "upload", file });
  assert.deepEqual(validateVideoSource({ youtubeUrl: cases.valid[2] }), { type: "youtube", youtubeUrl: cases.canonical });
});

test("controller rejects invalid input before touching MongoDB or forwarding", async () => {
  let databaseCalls = 0;
  const original = Job.create;
  Job.create = async () => { databaseCalls++; throw new Error("Unexpected database call"); };
  try {
    for (const youtubeUrl of cases.invalid) {
      const error = await new Promise((resolve) => {
        createJob({ body: { youtubeUrl }, userId: "test" }, {}, resolve);
      });
      assert.equal(error.statusCode, 400);
    }
    assert.equal(databaseCalls, 0);
  } finally {
    Job.create = original;
  }
});
