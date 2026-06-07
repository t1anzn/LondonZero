import dynamic from "next/dynamic";
import { useRef, useState } from "react";
import Dashboard from "@/components/Dashboard";
import { analyseStream, AnalyseResponse, Stage, StageStatus } from "@/lib/api";

// Leaflet requires browser APIs — load map client-side only
const JunctionMap = dynamic(() => import("@/components/JunctionMap"), { ssr: false });

const initialStages = (): Record<Stage, StageStatus> => ({
  data: "pending",
  vision: "pending",
  feasibility: "pending",
  recommendation: "pending",
  redesign: "pending",
});

export default function HomePage() {
  const [selected, setSelected] = useState<{ lat: number; lon: number; name: string }>({
    lat: 51.5133,
    lon: -0.0886,
    name: "Bank Junction",
  });
  const [result, setResult] = useState<AnalyseResponse | null>(null);
  const [stages, setStages] = useState<Record<Stage, StageStatus>>(initialStages());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  function handleLocationSelect(lat: number, lon: number, name: string) {
    setSelected({ lat, lon, name });
    setResult(null);
    setError(null);
    setStages(initialStages());
  }

  async function handleAnalyse() {
    if (!selected) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setLoading(true);
    setError(null);
    setResult({ summary: "" });
    setStages(initialStages());

    try {
      await analyseStream(
        "Why is this junction risky and what would make it safer?",
        { name: selected.name, lat: selected.lat, lon: selected.lon },
        {
          onStatus: (e) =>
            setStages((prev) => ({ ...prev, [e.stage]: e.state })),
          onStage: (stage, payload) =>
            setResult((prev) => ({ ...(prev ?? { summary: "" }), ...payload })),
          onError: (message) => setError(message),
          onDone: (final) => {
            setResult((prev) => ({ ...(prev ?? { summary: "" }), ...final }));
            setStages((prev) => {
              const next = { ...prev };
              (Object.keys(next) as Stage[]).forEach((s) => (next[s] = "done"));
              return next;
            });
          },
        },
        controller.signal
      );
    } catch (e) {
      if (!controller.signal.aborted) {
        setError(e instanceof Error ? e.message : "Unknown error");
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <Dashboard
      result={result}
      stages={stages}
      loading={loading}
      error={error}
      locationName={selected.name}
      onAnalyse={handleAnalyse}
      mapSlot={<JunctionMap onLocationSelect={handleLocationSelect} />}
    />
  );
}
