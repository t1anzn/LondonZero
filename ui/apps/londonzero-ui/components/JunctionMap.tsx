"use client";

import { useEffect } from "react";
import { MapContainer, TileLayer, CircleMarker, Popup, useMap } from "react-leaflet";

// Bank Junction — MVP is fixed to this location
const BANK = { lat: 51.5133, lon: -0.0886, name: "Bank Junction" };

function RecenterOnLoad() {
  const map = useMap();
  useEffect(() => {
    map.setView([BANK.lat, BANK.lon], 17);
  }, [map]);
  return null;
}

interface Props {
  onLocationSelect: (lat: number, lon: number, name: string) => void;
}

export default function JunctionMap({ onLocationSelect }: Props) {
  return (
    <MapContainer
      center={[BANK.lat, BANK.lon]}
      zoom={17}
      className="h-full w-full rounded-lg"
      // Leaflet CSS marker icon fix for Next.js
    >
      <RecenterOnLoad />
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />

      {/* Bank Junction — fixed hotspot for MVP */}
      {/* TODO: replace with dynamic heatmap layer from collision data */}
      <CircleMarker
        center={[BANK.lat, BANK.lon]}
        radius={18}
        pathOptions={{ color: "#ef4444", fillColor: "#ef4444", fillOpacity: 0.4 }}
        eventHandlers={{
          click: () => onLocationSelect(BANK.lat, BANK.lon, BANK.name),
        }}
      >
        <Popup>
          <strong>{BANK.name}</strong>
          <br />
          City of London&apos;s highest-casualty junction
          <br />
          <span className="text-xs text-gray-500">Click to analyse</span>
        </Popup>
      </CircleMarker>
    </MapContainer>
  );
}
