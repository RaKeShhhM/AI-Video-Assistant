import mongoose from "mongoose";

const schema = new mongoose.Schema({
  _id: String, // authenticated user ID
  day: String, // UTC date; counters reset atomically on the next admission
  secondsReserved: { type: Number, default: 0 },
  questions: { type: Number, default: 0 },
  activeJobs: [{ _id: false, jobId: String, day: String, seconds: Number, expiresAt: Date }],
}, { versionKey: false });

export default mongoose.model("ResourceUsage", schema);
