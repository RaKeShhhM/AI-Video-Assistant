const STAGE_LABELS = {
  queued: "Queued",
  downloading_audio: "Downloading audio",
  transcribing: "Transcribing",
  transcript_ready: "Transcript ready",
  generating_title: "Naming the video",
  summarizing: "Summarizing",
  summary_ready: "Summary ready",
  extracting_insights: "Extracting insights",
  insights_ready: "Insights ready",
  indexing_transcript: "Indexing for chat",
  completed: "Completed",
  error: "Failed",
};

export default function VUProgress({ status, stage, percent, segments = 20 }) {
  const litCount = Math.round((percent / 100) * segments);

  return (
    <div>
      <div className={`vu-meter ${status}`}>
        {Array.from({ length: segments }).map((_, i) => (
          <div key={i} className={`bar ${i < litCount ? "lit" : ""}`} style={{ height: `${40 + (i % 5) * 10}%` }} />
        ))}
      </div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          marginTop: 8,
          fontSize: 13,
        }}
      >
        <span className="dim">{STAGE_LABELS[stage] || stage}</span>
        <span className="mono dim">{percent}%</span>
      </div>
    </div>
  );
}
