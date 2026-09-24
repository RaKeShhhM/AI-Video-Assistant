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
      enum: ["queued", "processing", "completed", "failed"],
      default: "queued",
      index: true,
    },
    stage: { type: String, default: "queued" },
    percent: { type: Number, default: 0 },
    error: { type: String },
    durationSeconds: { type: Number },

    title: { type: String, default: "Untitled video" },
    transcript: { type: String },
    summary: { type: String },
    actionItems: { type: String },
    keyDecisions: { type: String },
    openQuestions: { type: String },

    chat: { type: [chatMessageSchema], default: [] },
  },
  { timestamps: true }
);

export default mongoose.model("Job", jobSchema);
