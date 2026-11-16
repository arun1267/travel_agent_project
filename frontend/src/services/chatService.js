const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/+$/, "");

export async function fetchCities(signal) {
  const response = await fetch(`${API_BASE_URL}/api/cities`, { signal });
  return response.ok ? response.json() : [];
}

export async function sendChatMessage(message, sessionId, trip = null) {
  const response = await fetch(`${API_BASE_URL}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      session_id: sessionId,
      trip: trip && {
        source: trip.from,
        destination: trip.to,
        trip_type: trip.tripType,
        travellers: trip.travellers,
        budget: trip.budget,
        start_date: trip.departure,
        end_date: trip.tripType === "round" ? trip.returnDate : null
      }
    })
  });

  if (!response.ok) {
    if (response.status === 422) {
      const error = await response.json();
      const detail = typeof error.detail === "string" ? error.detail : error.detail?.[0]?.msg;
      throw new Error(detail || "Check the trip details and try again.");
    }
    throw new Error("The travel service is temporarily unavailable. Please try again.");
  }

  return response.json();
}
