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
      {/* Esri World Imagery — keyless satellite tiles, matches the aerial look */}
      <TileLayer
        attribution="Tiles &copy; Esri — Source: Esri, Maxar, Earthstar Geographics"
        url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
        maxZoom={19}
      />

      {/* Bank Junction — fixed hotspot for MVP */}
      <CircleMarker
        center={[BANK.lat, BANK.lon]}
        radius={16}
        pathOptions={{ color: "#76b900", fillColor: "#76b900", fillOpacity: 0.25, weight: 2 }}
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
