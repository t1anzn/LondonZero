// Resolve the backend address. Priority:
//   1. NEXT_PUBLIC_API_URL if explicitly set (build-time override)
//   2. In the browser: same host the page was loaded from, on port 8000. This
//      makes the app portable across Tailscale IPs, scan-13.local on the LAN,
//      etc. — whatever address reached the frontend also reaches the backend.
//      (Access the UI via the machine's real address, NOT an SSH-forwarded
//      localhost, or the SSE stream gets buffered by the tunnel.)
//   3. SSR / fallback: localhost:8000
function apiUrl(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== "undefined") {
    return `${window.location.protocol}//${window.location.hostname}:8000`;
  }
  return "http://localhost:8000";
}

export interface LocationQuery {
  name: string;
  lat: number;
  lon: number;
  radius_m?: number;
}

// ── Structured agent outputs (mirror the backend pydantic models) ───────────
export interface CollisionProfile {
  location: string;
  total_collisions: number;
  fatal: number;
  serious: number;
  slight: number;
  cyclist_involved_pct: number;
  pedestrian_involved_pct: number;
  dominant_manoeuvre?: string | null;
  year_range?: [number, number] | null;
  osm_context?: Record<string, unknown>;
}

export interface HazardAssessment {
  image_url: string;
  hazards: string[];
  missing_infrastructure: string[];
  visibility_issues: string[];
  junction_complexity?: string | null;
  vlm_reasoning?: string | null;
  raw_vlm_response?: string;
}

export interface FeasibilityBrief {
  risk_factors: string[];
  infrastructure_constraints: string[];
  feasibility_score?: number | null;
  recommended_intervention?: string | null;
  design_brief: string;
  plain_explanation: string;
  confidence_notes: string;
  guidance_citations: string[];
}

export interface RedesignOutput {
  original_image_url: string;
  redesigned_image_b64: string;
  redesigned_image_url?: string; // optional URL fallback
  inpaint_prompt: string;
  design_brief: string;
  explanation: string;
}

export interface AnalyseResponse {
  summary: string;
  collision_profile?: CollisionProfile | null;
  hazard_assessment?: HazardAssessment | null;
  feasibility_brief?: FeasibilityBrief | null;
  redesign?: RedesignOutput | null;
  // populated by the recommendation stage during streaming
  original_image_url?: string;
  design_brief?: string;
}

// ── Streaming pipeline ──────────────────────────────────────────────────────
export type Stage = "data" | "vision" | "feasibility" | "recommendation" | "redesign";
export type StageState = "running" | "done";
export type StageStatus = "pending" | StageState;

export interface StatusEvent {
  stage: Stage;
  state: StageState;
  step: number;
  of: number;
}

export interface StreamHandlers {
  onStatus?: (e: StatusEvent) => void;
  onStage?: (stage: Stage, payload: Record<string, unknown>) => void;
  onDone?: (result: AnalyseResponse) => void;
  onError?: (message: string) => void;
}

/**
 * POST /analyse/stream and parse the SSE stream. EventSource is GET-only, so we
 * read the ReadableStream manually and split on SSE frame boundaries.
 */
export async function analyseStream(
  query: string,
  location: LocationQuery,
  handlers: StreamHandlers,
  signal?: AbortSignal
): Promise<void> {
  console.log("[stream] POST", `${apiUrl()}/analyse/stream`);
  const res = await fetch(`${apiUrl()}/analyse/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, location }),
    signal,
  });
  console.log("[stream] response", res.status, "body?", !!res.body);

  if (!res.ok || !res.body) {
    const err = await res.text().catch(() => res.statusText);
    throw new Error(`API error ${res.status}: ${err}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const dispatch = (frame: string) => {
    let event = "message";
    const dataLines: string[] = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (!dataLines.length) return;
    let data: Record<string, unknown>;
    try {
      data = JSON.parse(dataLines.join("\n"));
    } catch {
      return;
    }
    console.log("[stream] event:", event, data.stage ?? "");
    switch (event) {
      case "status":
        handlers.onStatus?.(data as unknown as StatusEvent);
        break;
      case "stage": {
        const { stage, ...payload } = data as { stage: Stage } & Record<string, unknown>;
        handlers.onStage?.(stage, payload);
        break;
      }
      case "done":
        handlers.onDone?.(data as unknown as AnalyseResponse);
        break;
      case "error":
        handlers.onError?.(String(data.message ?? "Unknown pipeline error"));
        break;
    }
  };

  let firstChunk = true;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    if (firstChunk) {
      console.log("[stream] first chunk received at", new Date().toISOString());
      firstChunk = false;
    }
    buffer += decoder.decode(value, { stream: true });
    let idx;
    // SSE frames are separated by a blank line.
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      if (frame.trim()) dispatch(frame);
    }
  }
}

export async function analyse(query: string, location: LocationQuery): Promise<AnalyseResponse> {
  const res = await fetch(`${apiUrl()}/analyse`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, location }),
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(`API error ${res.status}: ${err}`);
  }
  return res.json();
}

export async function healthCheck(): Promise<boolean> {
  try {
    const res = await fetch(`${apiUrl()}/health`);
    return res.ok;
  } catch {
    return false;
  }
}
