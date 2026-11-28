import { useEffect, useRef, useState } from "react";
import TripForm from "./TripForm.jsx";

export default function ChatPanel({ messages, loading, trip, onSend, onPlanTrip, onReset }) {
  const [value, setValue] = useState("");
  const messagesRef = useRef(null);

  useEffect(() => {
    const messageList = messagesRef.current;
    if (messageList) {
      const showTripForm = messages.length <= 1 && !loading && !trip;
      messageList.scrollTop = showTripForm ? 0 : messageList.scrollHeight;
    }
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
        {trip && <button type="button" className="new-trip-button" onClick={onReset} disabled={loading}>New trip</button>}
      </div>

      <div className="chat-messages" ref={messagesRef} role="region" aria-label="Chat messages" tabIndex={0}>
        {messages.map((message) => (
          <div className={`message-row ${message.role}`} key={message.id}>
            {message.role === "assistant" && <span className="mini-robot">🤖</span>}
            <div className="message-bubble">{message.content}</div>
          </div>
        ))}
        {!trip && <TripForm onSubmit={onPlanTrip} loading={loading} />}
        {loading && <div className="message-row assistant"><span className="mini-robot">🤖</span><div className="message-bubble loading">● ● ● &nbsp; Searching travel options...</div></div>}
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
