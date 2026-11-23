import { useRef, useState } from "react";
import Sidebar from "./components/Sidebar.jsx";
import ChatPanel from "./components/ChatPanel.jsx";
import ResultsPanel from "./components/ResultsPanel.jsx";
import { sendChatMessage, signIn, signOut, signUp } from "./services/chatService.js";
import "./styles.css";

const welcomeMessage = { id: "welcome", role: "assistant", content: "Great! I can help plan your trip. Fill in the details below to get started." };

function placesReply(result, answer) {
  const places = new Map();
  for (const place of result?.attractions || []) {
    const name = place.name?.trim();
    if (name) places.set(name.toLowerCase(), name);
  }
  if (!places.size) {
    return result === null
      ? "Places to visit are temporarily unavailable."
      : "No places to visit were returned for this trip. You can ask me to search again.";
  }
  const section = answer.split(/^## Places to visit\s*$/im)[1]?.split(/^#{1,3} /m)[0] || "";
  const descriptions = new Map();
  for (const line of section.split("\n")) {
    const match = line.match(/^\s*[-*]\s+(.+?)\s*\|\s*(.+)$/);
    if (!match) continue;
    const key = match[1].replace(/\*/g, "").trim().toLowerCase();
    const description = match[2].replace(/\b[1-9]\d{5}\b/g, "").trim();
    if (description) descriptions.set(key, description);
  }
  return ["Places to visit", ...Array.from(places, ([key, name]) => {
    const description = descriptions.get(key);
    return description ? `• ${name} — ${description}` : `• ${name}`;
  })].join("\n\n");
}

export default function App() {
  const [token, setToken] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [creatingAccount, setCreatingAccount] = useState(false);
  const [signingIn, setSigningIn] = useState(false);
  const [signingOut, setSigningOut] = useState(false);

  async function handleAuth(event) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    setError("");
    setNotice("");
    setSigningIn(true);
    try {
      const username = fields.get("username");
      const password = fields.get("password");
      if (creatingAccount) {
        if (password !== fields.get("confirmPassword")) throw new Error("Passwords do not match.");
        await signUp(username, password);
        setCreatingAccount(false);
        setNotice("Account created. Sign in to continue.");
      } else {
        const result = await signIn(username, password);
        setToken(result.access_token);
      }
    } catch (error) {
      setError(error instanceof TypeError ? "Could not connect to the travel service." : error.message);
    } finally {
      setSigningIn(false);
    }
  }

  async function handleSignOut() {
    setToken(null);
    setSigningOut(true);
    try {
      const response = await signOut(token);
      if (!response.ok) throw new Error("Sign-out failed");
    } catch {
      setError("Signed out on this device. The server could not revoke the session token.");
    } finally {
      setSigningOut(false);
    }
  }

  if (token) {
    return <Planner token={token} onSignOut={handleSignOut} onExpired={() => {
      setError("Your sign-in expired. Sign in again to continue.");
      setToken(null);
    }} />;
  }

  return (
    <main className="sign-in-page">
      <form key={creatingAccount ? "signup" : "login"} className="sign-in-card" onSubmit={handleAuth}>
        <h1>{creatingAccount ? "Create an account" : "Sign in"}</h1>
        <label>Username<input name="username" autoComplete="username" minLength={creatingAccount ? 3 : 1} maxLength={128} pattern={creatingAccount ? "[A-Za-z0-9_.\\-]+" : undefined} title={creatingAccount ? "Use letters, numbers, dots, underscores, or hyphens." : undefined} required /></label>
        <label>Password<input name="password" type="password" autoComplete={creatingAccount ? "new-password" : "current-password"} minLength={creatingAccount ? 12 : 1} maxLength={256} required /></label>
        {creatingAccount && <label>Confirm password<input name="confirmPassword" type="password" autoComplete="new-password" minLength={12} maxLength={256} required /></label>}
        {notice && <div role="status">{notice}</div>}
        {error && <p role="alert">{error}</p>}
        <button type="submit" disabled={signingIn || signingOut}>
          {creatingAccount ? "Sign up" : "Sign in"}
        </button>
        <button type="button" className="auth-switch" disabled={signingIn || signingOut} onClick={() => {
          setCreatingAccount(!creatingAccount);
          setError("");
          setNotice("");
        }}>
          {creatingAccount ? "Back to sign in" : "Create an account"}
        </button>
      </form>
    </main>
  );
}

function Planner({ token, onSignOut, onExpired }) {
  const [activePage, setActivePage] = useState("chat");
  const [activeTab, setActiveTab] = useState("flights");
  const [data, setData] = useState({});
  const [messages, setMessages] = useState([welcomeMessage]);
  const [loading, setLoading] = useState(false);
  const [trip, setTrip] = useState(null);

  const sessionId = useRef(null);
  const pending = useRef(false);

  function fmtDate(iso) {
    return new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
  }

  function handlePlanTrip(details) {
    const dates = details.tripType === "round" ? `${fmtDate(details.departure)} – ${fmtDate(details.returnDate)}` : fmtDate(details.departure);
    const budget = details.budget ? `, budget ₹${details.budget.toLocaleString("en-IN")}` : "";
    const summary = `${details.tripType === "round" ? "Round trip" : "One-way"}: ${details.from} → ${details.to}, ${dates}, ${details.travellers} traveller${details.travellers > 1 ? "s" : ""}${budget}`;
    setActiveTab("flights");
    handleSend(summary, details);
  }

  function handleReset() {
    if (pending.current) return;
    sessionId.current = null;
    setTrip(null);
    setData({});
    setActiveTab("flights");
    setMessages([welcomeMessage]);
  }

  async function handleSend(message, tripDetails = null) {
    if (pending.current) return;
    pending.current = true;
    setMessages((old) => [...old, { id: `${Date.now()}-user`, role: "user", content: message }]);
    setLoading(true);
    try {
      const response = await sendChatMessage(
        token,
        tripDetails ? "Plan this trip. Search for flights, accommodation when applicable, restaurants, and places to visit. Include a Places to visit section with a brief explanation of why each sight is known." : message,
        tripDetails ? null : sessionId.current,
        tripDetails
      );
      sessionId.current = response.session_id;
      const results = response.results;
      const showPlaces = response.outcome !== "error" && (
        tripDetails || (!data.destination && results.flights)
      );
      const content = showPlaces ? placesReply(results.attractions, response.answer) : response.answer;
      setMessages((old) => [...old, { id: `${Date.now()}-assistant`, role: "assistant", content }]);
      if (tripDetails && response.outcome !== "error") setTrip(tripDetails);
      setData((old) => {
        const destination = results.flights?.destination || tripDetails?.to ||
          results.attractions?.place_snapshot?.city || results.hotels?.destination ||
          results.restaurants?.destination || old.destination;
        const replace = tripDetails || (results.flights && old.destination && destination !== old.destination);
        return { ...(replace ? {} : old), ...results, destination };
      });
    } catch (error) {
      if (error.status === 401) {
        onExpired();
        return;
      }
      const content = error instanceof TypeError ? "I couldn't connect to the travel service. Please try again." : error.message;
      setMessages((old) => [...old, { id: `${Date.now()}-error`, role: "assistant", content }]);
    } finally {
      setLoading(false);
      pending.current = false;
    }
  }

  return (
    <div className="app-shell">
      <Sidebar activePage={activePage} onNavigate={setActivePage} />
      <main className="main-content">
      <div className="account-actions"><button type="button" onClick={onSignOut}>Sign out</button></div>
      <div className="mobile-brand"><img src="/image.png" alt="Travelling Trev" /></div>
        {activePage === "chat" ? (
          <div className="dashboard">
            <ChatPanel messages={messages} loading={loading} trip={trip} onSend={handleSend} onPlanTrip={handlePlanTrip} onReset={handleReset} />
            <ResultsPanel data={data} loading={loading} activeTab={activeTab} setActiveTab={setActiveTab} onAsk={handleSend} />
          </div>
        ) : (
          <section className="placeholder-page">
            <h1>My Trips</h1>
            <p>This section is ready for the next development step. Select Chat Planner to return to your travel assistant.</p>
            <button onClick={() => setActivePage("chat")}>Back to Chat Planner</button>
          </section>
        )}
      </main>
    </div>
  );
}
