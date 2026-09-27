import assert from "node:assert/strict";
import test from "node:test";
import multer from "multer";
import { uploadError } from "../src/utils/uploadError.js";
import { ApiError } from "../src/utils/ApiError.js";

test("upload errors separate malformed input from storage failures without leaking details", () => {
  for (const code of ["INVALID_FIELD_NAME", "MISSING_FIELD_NAME", "STREAM_DESTROYED", "LIMIT_FIELD_NESTING"]) {
    const result = uploadError(new multer.MulterError(code, "private-field"));
    assert.equal(result.statusCode, 400);
    assert.doesNotMatch(result.message, /private-field/);
  }
  assert.equal(uploadError(new multer.MulterError("LIMIT_FILE_SIZE")).statusCode, 413);
  assert.equal(uploadError(new Error("Unexpected end of form")).statusCode, 400);
  const disk = uploadError(Object.assign(new Error("ENOSPC /private/path"), { code: "ENOSPC" }));
  assert.equal(disk.statusCode, 500);
  assert.doesNotMatch(disk.message, /ENOSPC|private/);
  const policy = new ApiError(415, "Unsupported media type.");
  assert.equal(uploadError(policy), policy);
});
