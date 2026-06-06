// In development, calls go to the local Next.js mock route (/api/analyse).
// Set NEXT_PUBLIC_API_URL to the real FastAPI backend when agents are integrated.
const USE_MOCK = !process.env.NEXT_PUBLIC_API_URL && process.env.NODE_ENV === "development";
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface LocationQuery {
  name: string;
  lat: number;
  lon: number;
  radius_m?: number;
}

export interface RedesignOutput {
  original_image_url: string;
  redesigned_image_b64: string;   // base64 from real FLUX output
  redesigned_image_url?: string;  // URL fallback used by mock
  inpaint_prompt: string;
  design_brief: string;
  explanation: string;
}

export interface AnalyseResponse {
  summary: string;
  redesign: RedesignOutput | null;
}

export async function analyse(
  query: string,
  location: LocationQuery
): Promise<AnalyseResponse> {
  const url = USE_MOCK ? "/api/analyse" : `${API_URL}/analyse`;
  console.log("[api] analyse →", url, "USE_MOCK:", USE_MOCK);
  const res = await fetch(url, {
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
    const res = await fetch(`${API_URL}/health`);
    return res.ok;
  } catch {
    return false;
  }
}
