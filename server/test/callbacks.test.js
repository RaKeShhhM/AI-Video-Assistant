import assert from "node:assert/strict";
import test from "node:test";
import Job from "../src/models/Job.js";
import { usage } from "../src/services/usageService.js";
import { callbackInput } from "../src/utils/contracts.js";
import { receiveProgress } from "../src/controllers/videoController.js";
const event = { job_id: "a".repeat(24), attempt_id: "b".repeat(32), sequence: 3 };
const invoke = async body => {
  let response, error;
  await receiveProgress({ body }, { json: value => { response = value; } }, value => { error = value; });
  return { response, error };
};
test("callback references require identity/version and reject legacy result payloads", () => {
  assert.deepEqual(callbackInput(event), event);
  for (const patch of [{ attempt_id: null }, { attempt_id: "../bad" }, { sequence: "3" }, { sequence: 0 }, { sequence: 1.1 }, { sequence: Number.MAX_SAFE_INTEGER + 1 }, { data: {} }]) assert.throws(() => callbackInput({ ...event, ...patch }));
});
test("callbacks acknowledge saved state, retry failed side effects, ignore stale events and deduplicate", async t => {
  const job = { _id: event.job_id, user: "c".repeat(24), status: "completed", transcript: "durable result", progressVersion: 3, execution: { attemptId: event.attempt_id }, callbackAck: 0 };
  t.mock.method(Job, "findById", async () => job);
  const finish = t.mock.method(usage, "finishJob", async () => { throw new Error("temporary DB failure"); });
  const update = t.mock.method(Job, "findOneAndUpdate", async (filter, change) => {
    assert.equal(filter.progressVersion, 3);
    assert.equal(filter["execution.attemptId"], event.attempt_id);
    assert.deepEqual(change, { $set: { callbackAck: 3 } });
    job.callbackAck = 3;
    return job;
  });
  assert.ok((await invoke(event)).error);
  assert.equal(update.mock.callCount(), 0);
  finish.mock.mockImplementation(async () => {});
  assert.equal((await invoke(event)).response.received, true);
  assert.equal((await invoke(event)).response.received, true);
  assert.equal(update.mock.callCount(), 1);
  assert.equal(finish.mock.callCount(), 2);
  for (const patch of [{ sequence: 2 }, { sequence: 4 }, { attempt_id: "d".repeat(32) }]) assert.equal((await invoke({ ...event, ...patch })).response.ignored, true);
  assert.equal(update.mock.callCount(), 1);
  assert.equal(job.status, "completed");
  assert.equal(job.transcript, "durable result");
});
