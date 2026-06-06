import type { NextApiRequest, NextApiResponse } from "next";

// Mock response — swap this URL for the real backend once agents are integrated
// Images: real Mapillary-style street view of a London junction (placeholder URLs)
const MOCK_RESPONSE = {
  summary: `**Bank Junction — Road Safety Analysis**

Collision data (2019–2023): 166 recorded collisions, including 2 fatal, 31 serious, and 133 slight. Cyclists were involved in 48% of incidents — significantly above the London average.

**Key hazards identified from street imagery:**
- No dedicated cycle lane separation on the northbound approach along King William Street
- Pedestrian crossing on Threadneedle Street has limited sightlines due to parked vehicles
- High junction complexity — 5 arms converging with mixed signal phasing
- Lane markings faded on the Queen Victoria Street approach

**Recommended intervention:** Protected cycle infrastructure on the two highest-casualty arms (King William St and Cornhill), combined with improved pedestrian crossing visibility near the Monument entrance.

*This is a conceptual planning aid. Data confidence: medium — STATS19 records may under-report cycling incidents.*`,

  redesign: {
    // Real Mapillary street view image of Bank Junction area (public thumbnail)
    original_image_url:
      "https://upload.wikimedia.org/wikipedia/commons/thumb/3/3e/Bank_junction%2C_London.jpg/1280px-Bank_junction%2C_London.jpg",
    // Using a second image as a stand-in for the FLUX output until the model is integrated
    redesigned_image_url:
      "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e3/Cycling_infrastructure_London.jpg/1280px-Cycling_infrastructure_London.jpg",
    redesigned_image_b64: "",
    inpaint_prompt:
      "Add protected cycle lanes with green paint on King William Street approach, improve pedestrian crossing visibility, photorealistic London street, daytime",
    design_brief:
      "Add protected cycle lane separation on King William Street and Cornhill approaches. Widen pedestrian crossing refuge islands. Improve signal phasing visibility.",
    explanation:
      "The proposed redesign adds a protected cycle track on the two highest-casualty arms, separating cyclists from general traffic at the most conflict-prone points. Pedestrian crossing islands are widened to improve safety near the Monument station entrance.",
  },
};

export default function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method !== "POST") {
    return res.status(405).json({ error: "Method not allowed" });
  }

  // Simulate a short processing delay so the loading state is visible
  setTimeout(() => {
    res.status(200).json(MOCK_RESPONSE);
  }, 1800);
}
