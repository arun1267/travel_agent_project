import { useEffect, useState } from "react";
import { fetchDestinationImage, getDestinationImage } from "../data/destinationImages.js";

function SectionHeader({ icon, title }) {
  return <div className="section-header"><h2><span>{icon}</span>{title}</h2><button type="button" className="view-all">View all <b>→</b></button></div>;
}

function FlightCard({ flight }) {
  return (
    <article className="flight-card">
      <div className="airline-name">✈ {flight.airline}</div>
      <div className="flight-time"><strong>{flight.departureTime}</strong><small>{flight.departureCode}</small></div>
      <div className="flight-duration"><strong>{flight.duration}</strong><small>{flight.stops}</small></div>
      <div className="flight-time"><strong>{flight.arrivalTime}</strong><small>{flight.arrivalCode}</small></div>
      <div className="flight-price"><strong>₹{Number(flight.price).toLocaleString("en-IN")}</strong><small className="source-tag">{flight.source || "Unverified"}</small></div>
      <a className="book-button" href={flight.bookingUrl || undefined} target="_blank" rel="noreferrer">Book now ↗</a>
    </article>
  );
}

function StayCard({ item, restaurant = false }) {
  return (
    <article className="stay-card">
      <img src={item.image} alt={item.name} />
      <div className="stay-info">
        <h3>{item.name}</h3>
        <p className="rating">★ <span>{item.rating} ({item.reviews})</span></p>
        <p>📍 {item.location}</p>
        <div className="chips">{(restaurant ? item.features : item.amenities || []).map((feature) => <span key={feature}>{feature}</span>)}</div>
        <small className="source-tag">{item.source || "Unverified result"}</small>
      </div>
      {!restaurant && <div className="stay-price"><strong>₹{Number(item.price).toLocaleString("en-IN")}</strong><small>/ night</small></div>}
      {item.bookingUrl ? <a className="book-button" href={item.bookingUrl} target="_blank" rel="noreferrer">{restaurant ? "View place" : "Book now"} ↗</a> : <span className="no-link">No booking link</span>}
    </article>
  );
}

export default function ResultsPanel({ data, activeTab, setActiveTab }) {
  const tabs = [
    ["flights", "✈", "Flights"],
    ["hotels", "▰", "Hotels"],
    ["restaurants", "♨", "Restaurants"]
  ];

  const [wikiImage, setWikiImage] = useState(null);
  const knownImage = getDestinationImage(data.destination, data.image);

  useEffect(() => {
    let cancelled = false;
    setWikiImage(null);
    if (!knownImage && data.destination) {
      fetchDestinationImage(data.destination).then((url) => {
        if (!cancelled) setWikiImage(url);
      });
    }
    return () => { cancelled = true; };
  }, [data.destination, knownImage]);

  const heroImage = knownImage || wikiImage;

  return (
    <section className="results-panel">
      <div
        className="destination-hero"
        style={{
          backgroundImage: heroImage
            ? `linear-gradient(90deg, rgba(7,26,59,.56), rgba(7,26,59,.03)), url("${heroImage}")`
            : "linear-gradient(135deg, #1769e8, #3b8bff)"
        }}
      >
        <div className="hero-content">
          <h1>📍 {data.destination || "Your destination"}</h1>
          <p>{data.tagline || "Your next adventure starts here"}</p>
        </div>
        <button className="download-button" type="button" onClick={() => window.alert("PDF itinerary export can be added next.")}>⇩ Download itinerary</button>
      </div>

      <div className="tabs">
        {tabs.map(([id, icon, label]) => (
          <button key={id} type="button" onClick={() => setActiveTab(id)} className={activeTab === id ? "selected" : ""}>
            <span>{icon}</span>{label}
          </button>
        ))}
      </div>

      {activeTab === "flights" && (
        <section className="result-section">
          <SectionHeader icon="✈" title="Flight Options" />
          {data.flights?.length ? data.flights.map((flight) => <FlightCard key={flight.id} flight={flight} />) : <p className="empty-state">No flights found yet. Ask the assistant to search.</p>}
        </section>
      )}
      {activeTab === "hotels" && (
        <section className="result-section">
          <SectionHeader icon="▰" title="Top Hotels" />
          {data.hotels?.length ? data.hotels.map((hotel) => <StayCard key={hotel.id} item={hotel} />) : <p className="empty-state">No hotels found yet. Ask the assistant to search.</p>}
        </section>
      )}
      {activeTab === "restaurants" && (
        <section className="result-section">
          <SectionHeader icon="♨" title="Popular Restaurants" />
          {data.restaurants?.length ? data.restaurants.map((restaurant) => <StayCard key={restaurant.id} item={restaurant} restaurant />) : <p className="empty-state">No restaurants found yet. Ask the assistant to search.</p>}
        </section>
      )}

      <p className="disclaimer">Demo data is shown for layout preview. Verify live prices, availability and booking details with providers.</p>
    </section>
  );
}