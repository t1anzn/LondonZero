import dynamic from "next/dynamic";
import { useState } from "react";
import ResultsPanel from "@/components/ResultsPanel";
import { analyse, AnalyseResponse } from "@/lib/api";

// Leaflet requires browser APIs — load map client-side only
const JunctionMap = dynamic(() => import("@/components/JunctionMap"), { ssr: false });

export default function HomePage() {
  const [selected, setSelected] = useState<{
    lat: number;
    lon: number;
    name: string;
  }>({ lat: 51.5133, lon: -0.0886, name: "Bank Junction" });
  const [result, setResult] = useState<AnalyseResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function handleLocationSelect(lat: number, lon: number, name: string) {
    setSelected({ lat, lon, name });
    setResult(null);
    setError(null);
  }

  async function handleAnalyse() {
    if (!selected) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await analyse("Why is this junction risky and what would make it safer?", {
        name: selected.name,
        lat: selected.lat,
        lon: selected.lon,
      });
      setResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex h-screen bg-gray-900">
      {/* Left — map (takes most of the width) */}
      <div className="flex-1 p-3">
        <JunctionMap onLocationSelect={handleLocationSelect} />
      </div>

      {/* Right — results panel */}
      <div className="w-96 shrink-0 border-l border-gray-700 bg-gray-900">
        <ResultsPanel
          result={result}
          loading={loading}
          error={error}
          locationName={selected.name}
          onAnalyse={handleAnalyse}
        />
      </div>
    </div>
  );
}
