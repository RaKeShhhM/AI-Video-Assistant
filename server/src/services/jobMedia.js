import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import mongoose from "mongoose";

const BUCKET_NAME = "jobMedia";

function bucket() {
  if (!mongoose.connection.db) throw new Error("MongoDB is not connected");
  return new mongoose.mongo.GridFSBucket(mongoose.connection.db, { bucketName: BUCKET_NAME });
}

export async function storeJobUpload({ path, filename, contentType }) {
  const [metadata, digest] = await Promise.all([
    stat(path),
    new Promise((resolve, reject) => {
      const hash = createHash("sha256");
      const input = createReadStream(path);
      input.on("data", chunk => hash.update(chunk));
      input.once("error", reject);
      input.once("end", () => resolve(hash.digest("hex")));
    }),
  ]);
  const id = new mongoose.Types.ObjectId();
  const output = bucket().openUploadStreamWithId(id, filename, {
    contentType,
    metadata: { owner: "job", purpose: "processing-input" },
  });
  await new Promise((resolve, reject) => {
    const input = createReadStream(path);
    input.once("error", reject);
    output.once("error", reject);
    output.once("finish", resolve);
    input.pipe(output);
  });
  return {
    bucket: BUCKET_NAME,
    fileId: id,
    filename,
    contentType,
    size: metadata.size,
    sha256: digest,
  };
}

export async function deleteJobUpload(fileId) {
  if (!fileId) return;
  await bucket().delete(new mongoose.Types.ObjectId(fileId));
}

export async function reconcileOrphanJobUploads({ olderThanMs = 60 * 60 * 1000 } = {}) {
  const files = mongoose.connection.db.collection(`${BUCKET_NAME}.files`);
  const cutoff = new Date(Date.now() - olderThanMs);
  const cursor = files.find({
    "metadata.owner": "job",
    "metadata.purpose": "processing-input",
    uploadDate: { $lt: cutoff },
  }, { projection: { _id: 1 } });
  let removed = 0;
  for await (const file of cursor) {
    const referenced = await mongoose.connection.db.collection("jobs").findOne(
      { "sourceArtifact.fileId": file._id },
      { projection: { _id: 1 } },
    );
    if (!referenced) {
      await deleteJobUpload(file._id);
      removed++;
    }
  }
  return removed;
}
