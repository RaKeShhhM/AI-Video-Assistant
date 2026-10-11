import mongoose from "mongoose";

const chatMessageSchema = new mongoose.Schema(
  {
    messageId: { type: String, required: true, unique: true },
    job: { type: mongoose.Schema.Types.ObjectId, ref: "Job", required: true },
    user: { type: mongoose.Schema.Types.ObjectId, ref: "User", required: true },
    sequence: { type: Number, required: true },
    question: { type: String, required: true },
    answer: { type: String, required: true },
  },
  {
    timestamps: true,
    toJSON: {
      transform(_doc, ret) {
        delete ret.job;
        delete ret.user;
        return ret;
      },
    },
  }
);

chatMessageSchema.index({ job: 1, sequence: -1, _id: -1 });
chatMessageSchema.index({ job: 1, sequence: 1 }, { unique: true });

export default mongoose.model("ChatMessage", chatMessageSchema);
