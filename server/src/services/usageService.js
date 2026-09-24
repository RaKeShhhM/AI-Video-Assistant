import ResourceUsage from "../models/ResourceUsage.js";
import { limits } from "../config/resourceLimits.js";
import { ApiError } from "../utils/ApiError.js";

export function createUsageService(model = ResourceUsage, config = limits, clock = () => new Date()) {
  async function ensure(user) {
    try {
      await model.updateOne({ _id: String(user) }, { $setOnInsert: { day: "", secondsReserved: 0, questions: 0, activeJobs: [] } }, { upsert: true });
    } catch (err) { if (err.code !== 11000) throw err; } // concurrent first request
  }
  function expressions(now) {
    const day = now.toISOString().slice(0, 10);
    const today = { $eq: ["$day", day] };
    return { day,
      seconds: { $cond: [today, "$secondsReserved", 0] },
      questions: { $cond: [today, "$questions", 0] },
      active: { $filter: { input: "$activeJobs", as: "job", cond: { $gt: ["$$job.expiresAt", now] } } },
    };
  }
  return {
    async reserveJob(user, jobId) {
      await ensure(user);
      const now = clock();
      const e = expressions(now);
      const result = await model.findOneAndUpdate({ _id: String(user), $expr: { $and: [
        { $lt: [{ $size: e.active }, config.activeJobsPerUser] },
        { $lte: [{ $add: [e.seconds, config.mediaSeconds] }, config.dailySeconds] },
      ] } }, [{ $set: { day: e.day, secondsReserved: { $add: [e.seconds, config.mediaSeconds] },
        questions: e.questions, activeJobs: { $concatArrays: [e.active, [{ jobId: String(jobId), day: e.day,
          seconds: config.mediaSeconds, expiresAt: new Date(now.getTime() + config.leaseMs) }]] },
      } }], { new: true });
      if (!result) throw new ApiError(429, "Active-job limit or daily processing budget reached. Wait for your job to finish or for the UTC daily reset.");
    },
    async finishJob(user, jobId, durationSeconds) {
      // Removing the reservation in this same atomic update makes duplicate
      // callbacks harmless. Unknown duration keeps the conservative charge.
      const reservation = { $arrayElemAt: [{ $filter: { input: "$activeJobs", as: "j", cond: { $eq: ["$$j.jobId", String(jobId)] } } }, 0] };
      const measured = Number.isFinite(durationSeconds) && durationSeconds >= 0 ? Math.ceil(durationSeconds) : null;
      await model.updateOne({ _id: String(user), "activeJobs.jobId": String(jobId) }, [{ $set: {
        secondsReserved: { $let: { vars: { job: reservation }, in: { $max: [0, { $subtract: ["$secondsReserved",
          { $cond: [{ $and: [{ $eq: ["$day", "$$job.day"] }, measured !== null] },
            { $max: [0, { $subtract: ["$$job.seconds", measured ?? 0] }] }, 0] },
        ] }] } } },
        activeJobs: { $filter: { input: "$activeJobs", as: "j", cond: { $ne: ["$$j.jobId", String(jobId)] } } },
      } }]);
    },
    async reserveQuestion(user) {
      await ensure(user);
      const e = expressions(clock());
      const result = await model.findOneAndUpdate({ _id: String(user), $expr: { $lt: [e.questions, config.dailyQuestions] } },
        [{ $set: { day: e.day, secondsReserved: e.seconds, questions: { $add: [e.questions, 1] } } }], { new: true });
      if (!result) throw new ApiError(429, "Daily question budget reached. It resets at midnight UTC.");
    },
  };
}

export const usage = createUsageService();
