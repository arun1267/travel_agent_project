import { useEffect, useRef, useState } from "react";
import TripForm from "./TripForm.jsx";

export default function ChatPanel({ messages, loading, trip, onSend, onPlanTrip, onReset }) {
  const [value, setValue] = useState("");
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading, trip]);

  function submit(event) {
    event.preventDefault();
    const message = value.trim();
    if (!message || loading) return;
    onSend(message);
    setValue("");
  }

  return (
    <section className="chat-panel">
      <div className="welcome-row">
        <div className="robot-avatar">🤖</div>
        <div className="welcome-bubble">
          <strong>Hi! 👋</strong>
          <p>I'm your travel assistant.<br />Where would you like to go?</p>
        </div>
      </div>

      <div className="chat-messages">
        {messages.map((message) => (
          <div className={`message-row ${message.role}`} key={message.id}>
            {message.role === "assistant" && <span className="mini-robot">🤖</span>}
            <div className="message-bubble">{message.content}</div>
          </div>
        ))}
        {!trip && <TripForm onSubmit={onPlanTrip} />}
        {loading && <div className="message-row assistant"><span className="mini-robot">🤖</span><div className="message-bubble loading">● ● ● &nbsp; Searching travel options...</div></div>}
        <div ref={bottomRef} />
      </div>

      

      <form className="chat-input" onSubmit={submit}>
        <span className="attach-icon">⌁</span>
        <input
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder={trip ? "Ask anything about your trip..." : "Ask anything..."}
          aria-label="Ask the travel assistant"
        />
        <button type="submit" disabled={loading || !value.trim()} aria-label="Send message">➤</button>
      </form>
    </section>
  );
}
