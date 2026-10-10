import mongoose from "mongoose";

const chatMessageSchema = new mongoose.Schema(
  {
    question: { type: String, required: true },
    answer: { type: String, required: true },
  },
  { timestamps: true, _id: false }
);

const jobSchema = new mongoose.Schema(
  {
    user: { type: mongoose.Schema.Types.ObjectId, ref: "User", required: true, index: true },

    sourceType: { type: String, enum: ["youtube", "upload"], required: true },
    sourceValue: { type: String, required: true }, // YouTube URL or original filename
    language: { type: String, default: "english" },

    status: {
      type: String,
      enum: ["queued", "processing", "completed", "failed", "cancelled", "deleting", "deleted"],
      default: "queued",
      index: true,
    },
    stage: { type: String, default: "queued" },
    percent: { type: Number, default: 0 },
    error: { type: String },
    durationSeconds: { type: Number },

    title: { type: String, default: "Untitled video" },
    sourceArtifact: {
      bucket: { type: String, enum: ["jobMedia"] },
      fileId: { type: mongoose.Schema.Types.ObjectId },
      filename: { type: String },
      contentType: { type: String },
      size: { type: Number },
      sha256: { type: String },
    },
    retryOf: { type: mongoose.Schema.Types.ObjectId, ref: "Job" },
    indexAttempt: { type: String },
    submission: {
      fingerprint: String,
      token: String,
      leaseUntil: Date,
    },
    execution: {
      ready: { type: Boolean, default: true },
      attempts: { type: Number, default: 0 },
      maxAttempts: { type: Number, default: 3 },
      nextAttemptAt: { type: Date, default: Date.now },
      workerId: { type: String },
      attemptId: { type: String },
      leaseUntil: { type: Date },
      lastError: { type: String },
      checkpoint: { type: String, default: "queued" },
    },
    retention: { expiresAt: { type: Date, index: true } },
    deletion: {
      state: { type: String, enum: ["pending", "working", "failed", "done"] },
      requestedAt: Date,
      nextAt: Date,
      leaseUntil: Date,
      token: String,
      attempts: Number,
      lastError: String,
      activeAttemptId: String,
      workerStoppedAt: Date,
      waitUntil: Date,
      completedAt: Date,
    },
    progressVersion: { type: Number, default: 0 },
    callbackAck: { type: Number, default: 0 },
    delivery: {
      state: { type: String, enum: ["pending", "sending", "delivered", "blocked"] },
      attempts: { type: Number },
      nextAt: { type: Date },
      leaseUntil: { type: Date },
      token: { type: String },
      lastError: { type: String },
      deliveredAt: { type: Date },
    },
    transcript: { type: String },
    summary: { type: String },
    actionItems: { type: String },
    keyDecisions: { type: String },
    openQuestions: { type: String },

    chat: { type: [chatMessageSchema], default: [] },
  },
  {
    timestamps: true,
    toJSON: {
      transform(_doc, ret) {
        delete ret.sourceArtifact;
        delete ret.submission;
        delete ret.indexAttempt;
        if (ret.deletion) {
          delete ret.deletion.token;
          delete ret.deletion.activeAttemptId;
          delete ret.deletion.leaseUntil;
          delete ret.deletion.workerStoppedAt;
          delete ret.deletion.waitUntil;
        }
        delete ret.delivery;
        delete ret.callbackAck;
        if (["deleting", "deleted"].includes(ret.status)) {
          for (const field of ["sourceValue", "title", "transcript", "summary", "actionItems",
            "keyDecisions", "openQuestions", "chat", "error", "language", "sourceType"])
            delete ret[field];
        }
        if (ret.execution) {
          delete ret.execution.workerId;
          delete ret.execution.attemptId;
        }
        return ret;
      },
    },
  }
);

export default mongoose.model("Job", jobSchema);
