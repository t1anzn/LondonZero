import { useState, type ReactNode } from "react";
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
  mapSlot: ReactNode;
}

type Tab = "overview" | "risk" | "evidence" | "recommendations";
const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "risk", label: "Risk Analysis" },
  { id: "evidence", label: "Evidence" },
  { id: "recommendations", label: "Recommendations" },
];

const STEPS: { key: Stage; label: string }[] = [
  { key: "data", label: "Data" },
  { key: "vision", label: "Vision" },
  { key: "feasibility", label: "Feasibility" },
  { key: "recommendation", label: "Recommend" },
  { key: "redesign", label: "Redesign" },
];

// ── helpers ──────────────────────────────────────────────────────────────
const pctToCount = (pct: number, total: number) => Math.round((pct || 0) * (total || 0));

function complexityColor(c?: string | null): string {
  const v = (c || "").toLowerCase();
  if (v === "high") return "text-risk-high";
  if (v === "medium") return "text-risk-med";
  if (v === "low") return "text-risk-low";
  return "text-gray-400";
}

// Split a design brief paragraph into legend-style points (real text, no fake pins).
function briefPoints(brief?: string): string[] {
  if (!brief) return [];
  return brief
    .split(/(?<=[.;])\s+/)
    .map((s) => s.trim().replace(/\.$/, ""))
    .filter((s) => s.length > 8)
    .slice(0, 6);
}

// ── primitives ───────────────────────────────────────────────────────────
function Panel({ title, children, className = "" }: { title?: string; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl border border-edge bg-panel p-4 ${className}`}>
      {title && (
        <h3 className="mb-3 text-[11px] font-semibold uppercase tracking-wider text-gray-400">{title}</h3>
      )}
      {children}
    </section>
  );
}

function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className="h-3 animate-pulse rounded bg-panel2" style={{ width: `${90 - i * 12}%` }} />
      ))}
    </div>
  );
}

function Dot({ color }: { color: string }) {
  return <span className="mt-1.5 inline-block h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: color }} />;
}

function BulletList({ items, color = "#76b900" }: { items: string[]; color?: string }) {
  return (
    <ul className="space-y-2">
      {items.map((it, i) => (
        <li key={i} className="flex gap-2 text-sm text-gray-200">
          <Dot color={color} />
          <span>{it}</span>
        </li>
      ))}
    </ul>
  );
}

function Expander({ summary, children }: { summary: string; children: ReactNode }) {
  return (
    <details className="mt-3 rounded-lg bg-panel2/60 p-2">
      <summary className="cursor-pointer text-xs text-gray-400 hover:text-gray-200">{summary}</summary>
      <div className="mt-2 text-xs leading-relaxed text-gray-400">{children}</div>
    </details>
  );
}

// ── sidebar cards ────────────────────────────────────────────────────────
function ComplexityBadge({ hazards }: { hazards?: HazardAssessment | null }) {
  const c = hazards?.junction_complexity;
  if (!c) return null;
  return (
    <span className={`rounded-md border border-edge px-2 py-0.5 text-xs font-semibold ${complexityColor(c)}`}>
      {c} complexity
    </span>
  );
}

function CollisionSummary({ p }: { p?: CollisionProfile | null }) {
  if (!p) return <Panel title="Collision Summary"><Skeleton lines={2} /></Panel>;
  const years = p.year_range ? ` ${p.year_range[0]}–${p.year_range[1]}` : "";
  const cyclist = pctToCount(p.cyclist_involved_pct, p.total_collisions);
  const pedestrian = pctToCount(p.pedestrian_involved_pct, p.total_collisions);
  return (
    <Panel title={`Collision Summary${years ? ` (STATS19${years})` : ""}`}>
      <div className="grid grid-cols-3 gap-2 text-center">
        <div>
          <p className="text-2xl font-bold text-amber-400">{cyclist}</p>
          <p className="text-[10px] uppercase text-gray-400">Cyclist</p>
        </div>
        <div>
          <p className="text-2xl font-bold text-sky-400">{pedestrian}</p>
          <p className="text-[10px] uppercase text-gray-400">Pedestrian</p>
        </div>
        <div>
          <p className="text-2xl font-bold text-white">{p.total_collisions}</p>
          <p className="text-[10px] uppercase text-gray-400">Total</p>
        </div>
      </div>
      <div className="mt-3 flex justify-between border-t border-edge pt-2 text-xs text-gray-400">
        <span>Fatal <b className="text-risk-high">{p.fatal}</b></span>
        <span>Serious <b className="text-risk-med">{p.serious}</b></span>
        <span>Slight <b className="text-gray-200">{p.slight}</b></span>
      </div>
      {p.dominant_manoeuvre && (
        <p className="mt-2 text-[11px] text-gray-500">Most common manoeuvre: {p.dominant_manoeuvre}</p>
      )}
    </Panel>
  );
}

function TopRiskFactors({ feas, hazards }: { feas?: FeasibilityBrief | null; hazards?: HazardAssessment | null }) {
  const factors = feas?.risk_factors?.length ? feas.risk_factors : hazards?.hazards ?? [];
  return (
    <Panel title="Top Risk Factors">
      {factors.length ? <BulletList items={factors.slice(0, 6)} color="#ef4444" /> : <Skeleton lines={4} />}
    </Panel>
  );
}

function DataSources() {
  const sources = [
    "STATS19 Collision Data (DfT)",
    "Mapillary Street View",
    "OpenStreetMap road context",
    "TfL / LCDS planning guidance (RAG)",
  ];
  return (
    <Panel title="Data Sources">
      <ul className="space-y-1.5 text-sm text-gray-300">
        {sources.map((s) => (
          <li key={s} className="flex items-center gap-2">
            <span className="text-brand">✓</span>
            {s}
          </li>
        ))}
      </ul>
    </Panel>
  );
}

// ── center: redesign legend + before/after ───────────────────────────────
function RedesignLegend({ feas, redesign }: { feas?: FeasibilityBrief | null; redesign?: RedesignOutput | null }) {
  const intervention = feas?.recommended_intervention;
  const points = briefPoints(redesign?.design_brief || feas?.design_brief);
  return (
    <Panel title="Proposed Redesign">
      {intervention ? (
        <p className="mb-3 text-sm font-semibold text-brand">{intervention}</p>
      ) : (
        <Skeleton lines={1} />
      )}
      {points.length > 0 && <BulletList items={points} color="#76b900" />}
      {redesign?.inpaint_prompt && (
        <Expander summary="Show FLUX redesign prompt">{redesign.inpaint_prompt}</Expander>
      )}
    </Panel>
  );
}

function BeforeAfter({ result, redesignRunning }: { result: AnalyseResponse | null; redesignRunning: boolean }) {
  const before = result?.original_image_url ?? result?.redesign?.original_image_url;
  const r = result?.redesign;
  const after = r
    ? r.redesigned_image_url || (r.redesigned_image_b64 ? `data:image/jpeg;base64,${r.redesigned_image_b64}` : undefined)
    : undefined;
  return (
    <Panel title="Before / After">
      <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-3">
        <figure>
          {before ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={before} alt="Current junction" className="aspect-video w-full rounded-lg object-cover" />
          ) : (
            <div className="aspect-video w-full animate-pulse rounded-lg bg-panel2" />
          )}
          <figcaption className="mt-1 text-center text-[11px] text-gray-400">Before</figcaption>
        </figure>
        <div className="text-2xl text-brand">→</div>
        <figure>
          {after ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={after} alt="Proposed redesign" className="aspect-video w-full rounded-lg object-cover" />
          ) : (
            <div className="flex aspect-video w-full items-center justify-center rounded-lg bg-panel2 text-[11px] text-gray-400">
              {redesignRunning ? "FLUX rendering…" : "—"}
            </div>
          )}
          <figcaption className="mt-1 text-center text-[11px] text-gray-400">After (Proposed)</figcaption>
        </figure>
      </div>
      <p className="mt-2 text-center text-[10px] italic text-gray-500">
        Design concept for illustrative purposes only
      </p>
    </Panel>
  );
}

// ── right / recommendation content ───────────────────────────────────────
function FeasibilityGuidance({ feas }: { feas?: FeasibilityBrief | null }) {
  if (!feas) return <Panel title="Feasibility & Guidance"><Skeleton lines={4} /></Panel>;
  const score = feas.feasibility_score != null ? Math.round(feas.feasibility_score * 100) : null;
  return (
    <Panel title="Feasibility & Guidance">
      {score != null && (
        <div className="mb-3">
          <div className="flex items-end justify-between">
            <span className="text-sm text-gray-300">Intervention feasibility</span>
            <span className="text-xl font-bold text-brand">{score}%</span>
          </div>
          <div className="mt-1 h-2 overflow-hidden rounded-full bg-panel2">
            <div className="h-full rounded-full bg-brand" style={{ width: `${score}%` }} />
          </div>
        </div>
      )}
      {feas.recommended_intervention && (
        <p className="mb-3 text-sm text-gray-200">{feas.recommended_intervention}</p>
      )}
      {feas.infrastructure_constraints?.length > 0 && (
        <Expander summary={`Constraints (${feas.infrastructure_constraints.length})`}>
          <BulletList items={feas.infrastructure_constraints} color="#f59e0b" />
        </Expander>
      )}
      {feas.guidance_citations?.length > 0 && (
        <Expander summary={`Cited planning guidance (${feas.guidance_citations.length})`}>
          <ul className="space-y-2">
            {feas.guidance_citations.map((c, i) => (
              <li key={i} className="border-l-2 border-edge pl-2">{c}</li>
            ))}
          </ul>
        </Expander>
      )}
      {feas.confidence_notes && <Expander summary="Confidence & caveats">{feas.confidence_notes}</Expander>}
    </Panel>
  );
}

function RecommendationPanel({ result }: { result: AnalyseResponse | null }) {
  if (!result?.summary) return <Panel title="Recommendation"><Skeleton lines={5} /></Panel>;
  return (
    <Panel title="Orchestrator Recommendation">
      <p className="whitespace-pre-wrap text-sm leading-relaxed text-gray-200">{result.summary}</p>
    </Panel>
  );
}

function HazardsPanel({ hazards }: { hazards?: HazardAssessment | null }) {
  if (!hazards) return <Panel title="Identified Hazards"><Skeleton lines={4} /></Panel>;
  return (
    <Panel title="Identified Hazards (Vision)">
      {hazards.hazards.length > 0 && <BulletList items={hazards.hazards} color="#ef4444" />}
      {hazards.missing_infrastructure.length > 0 && (
        <>
          <p className="mb-1 mt-3 text-[11px] uppercase tracking-wider text-gray-500">Missing infrastructure</p>
          <BulletList items={hazards.missing_infrastructure} color="#f59e0b" />
        </>
      )}
      {hazards.visibility_issues.length > 0 && (
        <>
          <p className="mb-1 mt-3 text-[11px] uppercase tracking-wider text-gray-500">Visibility issues</p>
          <BulletList items={hazards.visibility_issues} color="#38bdf8" />
        </>
      )}
      {(hazards.vlm_reasoning || hazards.raw_vlm_response) && (
        <Expander summary="Show VLM reasoning">
          <pre className="whitespace-pre-wrap font-sans">{hazards.vlm_reasoning || hazards.raw_vlm_response}</pre>
        </Expander>
      )}
    </Panel>
  );
}

// ── top bar ──────────────────────────────────────────────────────────────
function TopBar({
  tab,
  setTab,
  onAnalyse,
  loading,
  stages,
}: {
  tab: Tab;
  setTab: (t: Tab) => void;
  onAnalyse: () => void;
  loading: boolean;
  stages: Record<Stage, StageStatus>;
}) {
  const runningStep = STEPS.find((s) => stages[s.key] === "running");
  return (
    <header className="flex items-center justify-between border-b border-edge bg-panel px-4 py-2">
      <div className="flex items-center gap-2">
        <span className="text-lg font-bold text-brand">◢</span>
        <div>
          <p className="text-sm font-bold leading-none text-white">LondonZero</p>
          <p className="text-[10px] text-gray-400">AI for Safer Streets</p>
        </div>
      </div>

      <nav className="flex gap-1">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
              tab === t.id ? "bg-brand/15 text-brand" : "text-gray-400 hover:text-gray-200"
            }`}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <div className="flex items-center gap-3">
        {loading && runningStep && (
          <span className="flex items-center gap-2 text-xs text-amber-300">
            <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-amber-400 border-t-transparent" />
            {runningStep.label}…
          </span>
        )}
        <button
          onClick={onAnalyse}
          disabled={loading}
          className="rounded-md bg-brand px-3 py-1.5 text-xs font-semibold text-black transition-opacity hover:opacity-90 disabled:opacity-40"
        >
          {loading ? "Analysing…" : "Run Analysis"}
        </button>
      </div>
    </header>
  );
}

function StageStrip({ stages }: { stages: Record<Stage, StageStatus> }) {
  return (
    <div className="flex items-center gap-3 px-1">
      {STEPS.map((s, i) => {
        const st = stages[s.key];
        const color = st === "done" ? "text-brand" : st === "running" ? "text-amber-400" : "text-gray-600";
        return (
          <div key={s.key} className="flex items-center gap-3">
            <span className={`flex items-center gap-1 text-[11px] ${color}`}>
              <span className={st === "running" ? "animate-pulse" : ""}>
                {st === "done" ? "●" : st === "running" ? "◐" : "○"}
              </span>
              {s.label}
            </span>
            {i < STEPS.length - 1 && <span className="text-gray-700">—</span>}
          </div>
        );
      })}
    </div>
  );
}

// ── main ─────────────────────────────────────────────────────────────────
export default function Dashboard({ result, stages, loading, error, locationName, onAnalyse, mapSlot }: Props) {
  const [tab, setTab] = useState<Tab>("overview");
  const started = loading || (result && Object.values(stages).some((s) => s !== "pending"));
  const redesignRunning = stages.redesign === "running";

  const sidebar = (
    <aside className="flex w-80 shrink-0 flex-col gap-3 overflow-y-auto border-r border-edge bg-ink p-3">
      <div className="rounded-xl border border-edge bg-panel p-4">
        <div className="mb-1 flex items-center justify-between">
          <h2 className="text-lg font-bold text-white">{locationName ?? "Bank Junction"}</h2>
          <ComplexityBadge hazards={result?.hazard_assessment} />
        </div>
        <p className="text-xs text-gray-400">City of London</p>
      </div>
      <CollisionSummary p={result?.collision_profile} />
      <TopRiskFactors feas={result?.feasibility_brief} hazards={result?.hazard_assessment} />
      <DataSources />
    </aside>
  );

  let center: ReactNode;
  let right: ReactNode;

  if (tab === "overview") {
    center = (
      <>
        <div className="relative min-h-[300px] flex-1 overflow-hidden rounded-xl border border-edge">
          {mapSlot}
        </div>
        <BeforeAfter result={result} redesignRunning={redesignRunning} />
      </>
    );
    right = (
      <>
        <RedesignLegend feas={result?.feasibility_brief} redesign={result?.redesign} />
        <FeasibilityGuidance feas={result?.feasibility_brief} />
      </>
    );
  } else if (tab === "risk") {
    center = (
      <>
        <CollisionSummary p={result?.collision_profile} />
        <HazardsPanel hazards={result?.hazard_assessment} />
      </>
    );
    right = <TopRiskFactors feas={result?.feasibility_brief} hazards={result?.hazard_assessment} />;
  } else if (tab === "evidence") {
    center = (
      <>
        <HazardsPanel hazards={result?.hazard_assessment} />
        <DataSources />
      </>
    );
    right = <FeasibilityGuidance feas={result?.feasibility_brief} />;
  } else {
    // recommendations
    center = (
      <>
        <RecommendationPanel result={result} />
        <BeforeAfter result={result} redesignRunning={redesignRunning} />
      </>
    );
    right = (
      <>
        <RedesignLegend feas={result?.feasibility_brief} redesign={result?.redesign} />
        <FeasibilityGuidance feas={result?.feasibility_brief} />
      </>
    );
  }

  return (
    <div className="flex h-screen flex-col bg-ink text-gray-200">
      <TopBar tab={tab} setTab={setTab} onAnalyse={onAnalyse} loading={loading} stages={stages} />
      {started && (
        <div className="border-b border-edge bg-panel/60 px-4 py-1.5">
          <StageStrip stages={stages} />
        </div>
      )}
      {error && (
        <div className="border-b border-red-800 bg-red-950/70 px-4 py-2 text-sm text-red-300">{error}</div>
      )}
      <div className="flex flex-1 overflow-hidden">
        {sidebar}
        <main className="flex flex-1 flex-col gap-3 overflow-y-auto p-3">{center}</main>
        <aside className="flex w-80 shrink-0 flex-col gap-3 overflow-y-auto border-l border-edge bg-ink p-3">
          {right}
        </aside>
      </div>
    </div>
  );
}
