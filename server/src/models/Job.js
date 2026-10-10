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
      enum: ["queued", "processing", "completed", "failed", "cancelled"],
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
        delete ret.delivery;
        delete ret.callbackAck;
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
