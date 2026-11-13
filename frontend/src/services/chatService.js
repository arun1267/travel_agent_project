import { demoTrip } from "../data/demoData";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
const USE_DEMO_MODE = true;

const AIRPORT_CODES = {
  Ahmedabad: "AMD", Bengaluru: "BLR", Chennai: "MAA", Delhi: "DEL", Goa: "GOI",
  Hyderabad: "HYD", Jaipur: "JAI", Kochi: "COK", Kolkata: "CCU", Mumbai: "BOM",
  Pune: "PNQ", Udaipur: "UDR", Varanasi: "VNS", Visakhapatnam: "VTZ"
};

export async function sendChatMessage(message, sessionId = "travelbuddy-demo", trip = null) {
  if (USE_DEMO_MODE) {
    await new Promise((resolve) => setTimeout(resolve, 650));
    const base = { ...demoTrip };
    if (trip) {
      base.destination = trip.to;
      base.tagline = `${trip.from} → ${trip.to}`;
      base.flights = demoTrip.flights.map((f) => ({
        ...f,
        departureCode: AIRPORT_CODES[trip.from] || f.departureCode,
        arrivalCode: AIRPORT_CODES[trip.to] || f.arrivalCode
      }));
    }
    return {
      answer: trip
        ? `Got it! Here are demo options for ${trip.from} → ${trip.to}. These are sample results for the frontend preview, not live prices.`
        : `I've prepared some travel options based on your message: "${message}". These are demo results for the frontend preview, not live prices.`,
      ...base
    };
  }

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
    throw new Error(`Backend request failed: ${response.status}`);
  }

  return response.json();
}
