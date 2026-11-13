import { useState } from "react";
import Sidebar from "./components/Sidebar.jsx";
import ChatPanel from "./components/ChatPanel.jsx";
import ResultsPanel from "./components/ResultsPanel.jsx";
import { demoTrip } from "./data/demoData.js";
import { sendChatMessage } from "./services/chatService.js";
import "./styles.css";

export default function App() {
  const [activePage, setActivePage] = useState("chat");
  const [activeTab, setActiveTab] = useState("flights");
  const [data, setData] = useState(demoTrip);
  const [messages, setMessages] = useState([
    { id: "welcome", role: "assistant", content: "Great! I can help plan your trip. Fill in the details below to get started." }
  ]);
  const [loading, setLoading] = useState(false);
  const [trip, setTrip] = useState(null);

  const welcomeMessage = { id: "welcome", role: "assistant", content: "Great! I can help plan your trip. Fill in the details below to get started." };

  function fmtDate(iso) {
    return new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
  }

  function handlePlanTrip(details) {
    setTrip(details);
    const dates = details.tripType === "round" ? `${fmtDate(details.departure)} – ${fmtDate(details.returnDate)}` : fmtDate(details.departure);
    const budget = details.budget ? `, budget ₹${details.budget.toLocaleString("en-IN")}` : "";
    const summary = `${details.tripType === "round" ? "Round trip" : "One-way"}: ${details.from} → ${details.to}, ${dates}, ${details.travellers} traveller${details.travellers > 1 ? "s" : ""}${budget}`;
    setActiveTab("flights");
    handleSend(summary, details);
  }

  function handleReset() {
    setTrip(null);
    setData(demoTrip);
    setActiveTab("flights");
    setMessages([welcomeMessage]);
  }

  async function handleSend(message, tripDetails = trip) {
    setMessages((old) => [...old, { id: `${Date.now()}-user`, role: "user", content: message }]);
    setLoading(true);
    try {
      const response = await sendChatMessage(message, "travelbuddy-demo", tripDetails);
      setMessages((old) => [...old, { id: `${Date.now()}-assistant`, role: "assistant", content: response.answer }]);
      setData((old) => ({ ...old, ...response }));
    } catch (error) {
      setMessages((old) => [...old, { id: `${Date.now()}-error`, role: "assistant", content: "I couldn't connect to the travel service. Please try again." }]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="app-shell">
      <Sidebar activePage={activePage} onNavigate={setActivePage} />
      <main className="main-content">
      <div className="mobile-brand"><img src="/image.png" alt="Travelling Trev" /></div>
        {activePage === "chat" ? (
          <div className="dashboard">
            <ChatPanel messages={messages} loading={loading} trip={trip} onSend={handleSend} onPlanTrip={handlePlanTrip} onReset={handleReset} />
            <ResultsPanel data={data} activeTab={activeTab} setActiveTab={setActiveTab} />
          </div>
        ) : (
          <section className="placeholder-page">
            <h1>{activePage === "trips" ? "My Trips" : activePage === "favorites" ? "Favorites" : "Settings"}</h1>
            <p>This section is ready for the next development step. Select Chat Planner to return to your travel assistant.</p>
            <button onClick={() => setActivePage("chat")}>Back to Chat Planner</button>
          </section>
        )}
      </main>
    </div>
  );
}
