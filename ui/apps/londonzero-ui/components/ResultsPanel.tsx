import { AnalyseResponse } from "@/lib/api";

interface Props {
  result: AnalyseResponse | null;
  loading: boolean;
  error: string | null;
  locationName: string | null;
  onAnalyse: () => void;
}

export default function ResultsPanel({
  result,
  loading,
  error,
  locationName,
  onAnalyse,
}: Props) {
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
        className="rounded-lg bg-brand px-4 py-2 font-semibold text-black disabled:opacity-40 hover:opacity-90 transition-opacity"
      >
        {loading ? "Analysing…" : "Run Analysis"}
      </button>

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-red-700 bg-red-950 p-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {/* Results */}
      {result && (
        <>
          {/* Summary */}
          <div className="rounded-lg bg-gray-800 p-4">
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-brand">
              Analysis
            </h2>
            <p className="whitespace-pre-wrap text-sm text-gray-200">{result.summary}</p>
          </div>

          {/* Before / After images */}
          {result.redesign && (
            <div className="rounded-lg bg-gray-800 p-4">
              <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-brand">
                Road Redesign
              </h2>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <p className="mb-1 text-xs text-gray-400">Before</p>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={result.redesign.original_image_url}
                    alt="Current junction"
                    className="w-full rounded object-cover"
                  />
                </div>
                <div>
                  <p className="mb-1 text-xs text-gray-400">Proposed</p>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={
                      result.redesign.redesigned_image_url ||
                      `data:image/jpeg;base64,${result.redesign.redesigned_image_b64}`
                    }
                    alt="Proposed redesign"
                    className="w-full rounded object-cover"
                  />
                </div>
              </div>
              <p className="mt-3 text-sm text-gray-300">{result.redesign.explanation}</p>
              <p className="mt-2 text-xs text-gray-500 italic">
                This is a conceptual planning aid, not a final engineering design.
              </p>
            </div>
          )}
        </>
      )}

      {/* Empty state */}
      {!result && !loading && !error && locationName && (
        <p className="text-sm text-gray-500">Press &quot;Run Analysis&quot; to start.</p>
      )}
    </div>
  );
}
