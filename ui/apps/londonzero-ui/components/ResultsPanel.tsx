import {
  AnalyseResponse,
  Stage,
  StageStatus,
  CollisionProfile,
  HazardAssessment,
  FeasibilityBrief,
  RedesignOutput,
} from "@/lib/api";

interface Props {
  result: AnalyseResponse | null;
  stages: Record<Stage, StageStatus>;
  loading: boolean;
  error: string | null;
  locationName: string | null;
  onAnalyse: () => void;
}

const STEPS: { key: Stage; label: string; running: string }[] = [
  { key: "data", label: "Data", running: "Retrieving collision data & street imagery…" },
  { key: "vision", label: "Vision", running: "Analysing street image for hazards…" },
  { key: "feasibility", label: "Feasibility", running: "Assessing interventions against planning guidance…" },
  { key: "recommendation", label: "Recommend", running: "Synthesising the recommendation…" },
  { key: "redesign", label: "Redesign", running: "Rendering the redesign with FLUX (this is the slow one)…" },
];

function dot(status: StageStatus): string {
  if (status === "done") return "●";
  if (status === "running") return "◐";
  return "○";
}

function dotColor(status: StageStatus): string {
  if (status === "done") return "text-brand";
  if (status === "running") return "text-yellow-400 animate-pulse";
  return "text-gray-600";
}

function Stepper({ stages }: { stages: Record<Stage, StageStatus> }) {
  return (
    <div className="flex items-center justify-between rounded-lg bg-gray-800 px-3 py-2">
      {STEPS.map((s) => (
        <div key={s.key} className="flex flex-col items-center gap-1">
          <span className={`text-lg leading-none ${dotColor(stages[s.key])}`}>
            {dot(stages[s.key])}
          </span>
          <span className="text-[10px] text-gray-400">{s.label}</span>
        </div>
      ))}
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg bg-gray-800 p-4">
      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-brand">{title}</h2>
      {children}
    </div>
  );
}

function Details({ summary, children }: { summary: string; children: React.ReactNode }) {
  return (
    <details className="mt-3 group">
      <summary className="cursor-pointer text-xs text-gray-400 hover:text-gray-200">
        {summary}
      </summary>
      <div className="mt-2 text-xs text-gray-400">{children}</div>
    </details>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded bg-gray-900/60 p-2 text-center">
      <p className="text-lg font-bold text-white">{value}</p>
      <p className="text-[10px] uppercase tracking-wide text-gray-400">{label}</p>
    </div>
  );
}

function CollisionStatsCard({ p }: { p: CollisionProfile }) {
  const years = p.year_range ? `${p.year_range[0]}–${p.year_range[1]}` : null;
  return (
    <Card title="Collision Data">
      <div className="grid grid-cols-4 gap-2">
        <Stat label="Total" value={p.total_collisions} />
        <Stat label="Fatal" value={p.fatal} />
        <Stat label="Serious" value={p.serious} />
        <Stat label="Slight" value={p.slight} />
      </div>
      <div className="mt-2 grid grid-cols-2 gap-2">
        <Stat label="Cyclist %" value={`${Math.round(p.cyclist_involved_pct * 100)}%`} />
        <Stat label="Pedestrian %" value={`${Math.round(p.pedestrian_involved_pct * 100)}%`} />
      </div>
      <p className="mt-2 text-xs text-gray-400">
        {p.location}
        {years ? ` · STATS19 ${years}` : ""}
        {p.dominant_manoeuvre ? ` · most common manoeuvre: ${p.dominant_manoeuvre}` : ""}
      </p>
    </Card>
  );
}

function List({ items }: { items: string[] }) {
  return (
    <ul className="list-disc space-y-1 pl-4 text-sm text-gray-200">
      {items.map((it, i) => (
        <li key={i}>{it}</li>
      ))}
    </ul>
  );
}

function VisionCard({ h }: { h: HazardAssessment }) {
  return (
    <Card title="Vision — Hazards Identified">
      {h.junction_complexity && (
        <p className="mb-2 text-xs text-gray-400">
          Junction complexity:{" "}
          <span className="font-semibold text-gray-200">{h.junction_complexity}</span>
        </p>
      )}
      {h.hazards.length > 0 ? <List items={h.hazards} /> : <p className="text-sm text-gray-500">No hazards listed.</p>}
      {h.missing_infrastructure.length > 0 && (
        <div className="mt-3">
          <p className="mb-1 text-xs uppercase tracking-wide text-gray-500">Missing infrastructure</p>
          <List items={h.missing_infrastructure} />
        </div>
      )}
      {h.visibility_issues.length > 0 && (
        <div className="mt-3">
          <p className="mb-1 text-xs uppercase tracking-wide text-gray-500">Visibility issues</p>
          <List items={h.visibility_issues} />
        </div>
      )}
      {(h.vlm_reasoning || h.raw_vlm_response) && (
        <Details summary="Show VLM reasoning">
          <pre className="whitespace-pre-wrap font-sans">{h.vlm_reasoning || h.raw_vlm_response}</pre>
        </Details>
      )}
    </Card>
  );
}

function FeasibilityCard({ f }: { f: FeasibilityBrief }) {
  const score = f.feasibility_score != null ? `${Math.round(f.feasibility_score * 100)}%` : "—";
  return (
    <Card title="Feasibility Assessment">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-sm text-gray-200">{f.recommended_intervention ?? "—"}</span>
        <span className="rounded bg-brand/20 px-2 py-0.5 text-xs font-semibold text-brand">
          {score} feasible
        </span>
      </div>
      {f.risk_factors.length > 0 && (
        <div className="mb-3">
          <p className="mb-1 text-xs uppercase tracking-wide text-gray-500">Risk factors</p>
          <List items={f.risk_factors} />
        </div>
      )}
      {f.infrastructure_constraints.length > 0 && (
        <div className="mb-3">
          <p className="mb-1 text-xs uppercase tracking-wide text-gray-500">Constraints</p>
          <List items={f.infrastructure_constraints} />
        </div>
      )}
      {f.guidance_citations.length > 0 && (
        <Details summary={`Show planning guidance cited (${f.guidance_citations.length})`}>
          <ul className="space-y-2">
            {f.guidance_citations.map((c, i) => (
              <li key={i} className="border-l-2 border-gray-700 pl-2">
                {c}
              </li>
            ))}
          </ul>
        </Details>
      )}
      {f.confidence_notes && <Details summary="Confidence & caveats">{f.confidence_notes}</Details>}
    </Card>
  );
}

function RecommendationCard({
  summary,
  designBrief,
  redesign,
}: {
  summary: string;
  designBrief?: string;
  redesign?: RedesignOutput | null;
}) {
  return (
    <Card title="Orchestrator Recommendation">
      <p className="whitespace-pre-wrap text-sm text-gray-200">{summary}</p>
      {(designBrief || redesign?.inpaint_prompt) && (
        <Details summary="Show design brief & FLUX prompt">
          {designBrief && (
            <p className="mb-2">
              <span className="text-gray-500">Design brief: </span>
              {designBrief}
            </p>
          )}
          {redesign?.inpaint_prompt && (
            <p>
              <span className="text-gray-500">FLUX prompt: </span>
              {redesign.inpaint_prompt}
            </p>
          )}
        </Details>
      )}
    </Card>
  );
}

function BeforeAfterCard({
  beforeUrl,
  redesign,
  pending,
}: {
  beforeUrl?: string;
  redesign?: RedesignOutput | null;
  pending: boolean;
}) {
  const afterSrc = redesign
    ? redesign.redesigned_image_url ||
      (redesign.redesigned_image_b64
        ? `data:image/jpeg;base64,${redesign.redesigned_image_b64}`
        : undefined)
    : undefined;
  return (
    <Card title="Road Redesign">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <p className="mb-1 text-xs text-gray-400">Before</p>
          {beforeUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={beforeUrl} alt="Current junction" className="w-full rounded object-cover" />
          ) : (
            <div className="aspect-video animate-pulse rounded bg-gray-700" />
          )}
        </div>
        <div>
          <p className="mb-1 text-xs text-gray-400">Proposed</p>
          {afterSrc ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={afterSrc} alt="Proposed redesign" className="w-full rounded object-cover" />
          ) : (
            <div className="flex aspect-video items-center justify-center rounded bg-gray-700 text-center text-[10px] text-gray-400">
              {pending ? "FLUX rendering…" : "—"}
            </div>
          )}
        </div>
      </div>
      {redesign?.explanation && <p className="mt-3 text-sm text-gray-300">{redesign.explanation}</p>}
      <p className="mt-2 text-xs italic text-gray-500">
        Conceptual planning aid, not a final engineering design.
      </p>
    </Card>
  );
}

export default function ResultsPanel({
  result,
  stages,
  loading,
  error,
  locationName,
  onAnalyse,
}: Props) {
  const started = loading || (result && Object.values(stages).some((s) => s !== "pending"));
  const beforeUrl = result?.original_image_url ?? result?.redesign?.original_image_url;
  const redesignPending = stages.redesign === "running";
  const runningStep = STEPS.find((s) => stages[s.key] === "running");

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-4">
      {/* Header */}
      <div>
        <h1 className="text-xl font-bold text-white">LondonZero</h1>
        <p className="text-sm text-gray-400">Road Risk &amp; Redesign Assistant</p>
      </div>

      {/* Selected location */}
      <div className="rounded-lg bg-gray-800 p-3">
        <p className="text-xs text-gray-400">Selected junction</p>
        <p className="font-medium text-white">{locationName ?? "Click a junction on the map"}</p>
      </div>

      {/* Analyse button */}
      <button
        onClick={onAnalyse}
        disabled={!locationName || loading}
        className="rounded-lg bg-brand px-4 py-2 font-semibold text-black transition-opacity hover:opacity-90 disabled:opacity-40"
      >
        {loading ? "Analysing…" : "Run Analysis"}
      </button>

      {/* Progress stepper */}
      {started && <Stepper stages={stages} />}

      {/* Current running stage — makes slow stages visibly active */}
      {loading && runningStep && (
        <div className="flex items-center gap-2 rounded-lg border border-yellow-700/50 bg-yellow-950/30 px-3 py-2 text-sm text-yellow-200">
          <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-yellow-400 border-t-transparent" />
          {runningStep.running}
        </div>
      )}

      {/* Error (partials below still render) */}
      {error && (
        <div className="rounded-lg border border-red-700 bg-red-950 p-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {/* Cards — each appears as its stage completes */}
      {result?.collision_profile && <CollisionStatsCard p={result.collision_profile} />}
      {result?.hazard_assessment && <VisionCard h={result.hazard_assessment} />}
      {result?.feasibility_brief && <FeasibilityCard f={result.feasibility_brief} />}
      {result?.summary && (
        <RecommendationCard
          summary={result.summary}
          designBrief={result.design_brief}
          redesign={result.redesign}
        />
      )}
      {(beforeUrl || result?.redesign) && (
        <BeforeAfterCard beforeUrl={beforeUrl} redesign={result?.redesign} pending={redesignPending} />
      )}

      {/* Empty state */}
      {!started && !error && locationName && (
        <p className="text-sm text-gray-500">Press &quot;Run Analysis&quot; to start.</p>
      )}
    </div>
  );
}
