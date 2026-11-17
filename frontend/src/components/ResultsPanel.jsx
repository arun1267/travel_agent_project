import { useState } from "react";
import DestinationHero from "./DestinationHero.jsx";
import { resultImages } from "../data/resultImages.js";

function ResultImage({ category, url, logo = false }) {
  const [failedUrl, setFailedUrl] = useState(null);
  const source = typeof url === "string" && url.startsWith("https://") && url !== failedUrl ? url : null;
  const fallback = resultImages[category];
  return (
    <figure className={`result-image${logo && source ? " airline-logo" : ""}`}>
      {(source || failedUrl !== fallback) && (
        <img src={source || fallback} alt="" loading="lazy" onError={() => setFailedUrl(source || fallback)} />
      )}
      {!source && <figcaption>Illustrative photo</figcaption>}
    </figure>
  );
}

function ExternalLink({ url, children }) {
  if (!url || !/^https:\/\//i.test(url)) return null;
  return <a className="book-button" href={url} target="_blank" rel="noopener noreferrer">{children} ↗</a>;
}

function formatPrice(price, currency = "INR") {
  if (typeof price !== "number") return "Price unavailable";
  return new Intl.NumberFormat("en-IN", { style: "currency", currency, maximumFractionDigits: 2 }).format(price);
}

function EmptyResults({ label, result, loading }) {
  let message = `Ask the assistant to search for ${label}.`;
  if (loading) message = "Checking travel options…";
  else if (result === null) message = `${label[0].toUpperCase() + label.slice(1)} are temporarily unavailable.`;
  else if (result) message = `No ${label} found for this search.`;
  return <p className="empty-state" role="status">{message}</p>;
}

function FlightCard({ flight, currency, fareType }) {
  const segments = flight.segments || [];
  const fareLabel = fareType === "one_way" ? "One-way fare"
    : flight.outbound_id != null ? "Round-trip total" : "Round-trip fare from";
  const carriers = [...new Map(segments.filter((segment) => segment.airline_website)
    .map((segment) => [segment.airline_website, segment.airline])).entries()];
  return (
    <article className="stay-card live-flight-card">
      <ResultImage category="flights" url={flight.airline_logo} logo />
      <div className="stay-info flight-details">
        {flight.outbound_id != null && <p className="flight-note">Pairs with outbound option {flight.outbound_id}</p>}
        {segments.map((segment, index) => (
          <div className="flight-segment" key={index}>
            <div className="flight-segment-title">
              <strong>{segment.airline || "Flight"}</strong>
              {segment.flight_number && <span className="flight-number">{segment.flight_number}</span>}
            </div>
            <div className="flight-timing">
              <div><span>Departure</span><strong>{segment.departure_time || "Time unavailable"}</strong><span>{segment.from}</span></div>
              <div><span>Arrival</span><strong>{segment.arrival_time || "Time unavailable"}</strong><span>{segment.to}</span></div>
            </div>
          </div>
        ))}
        {flight.total_duration_minutes != null && <p className="flight-note">{flight.total_duration_minutes} min total</p>}
      </div>
      <div className="stay-price">
        <strong>{formatPrice(flight.price, currency)}</strong>
        <small>{fareLabel}</small>
      </div>
      <div className="result-links">
        {carriers.map(([url, name]) => <ExternalLink key={url} url={url}>Visit {name}</ExternalLink>)}
        {!carriers.length && (flight.booking_link
          ? <ExternalLink url={flight.booking_link}>Visit booking site</ExternalLink>
          : <ExternalLink url={flight.search_link}>Search flights</ExternalLink>)}
      </div>
    </article>
  );
}

function FlightSection({ leg, title, currency }) {
  if (!leg) return null;
  return (
    <section className="result-section">
      <div className="section-header"><h2>{title}</h2></div>
      <p className="flight-route">{leg.source} → {leg.destination} · {leg.date}</p>
      {leg.options.length ? leg.options.map((flight, index) => (
        <div key={flight.id ?? index} className="flight-option">
          {flight.id != null && <p className="flight-note flight-option-label">Outbound option {flight.id}</p>}
          <FlightCard flight={flight} currency={currency} fareType={leg.fare_type} />
        </div>
      )) : <p className="empty-state">No options available for this leg.</p>}
    </section>
  );
}

function StayCard({ item, restaurant }) {
  return (
    <article className="stay-card">
      <ResultImage category={restaurant ? "restaurants" : "hotels"} url={item.image_url} />
      <div className="stay-info">
        <h3>{item.name || (restaurant ? "Restaurant" : "Hotel")}</h3>
        {item.overall_rating != null && <p>★ {item.overall_rating}{item.reviews != null && ` (${item.reviews} reviews)`}</p>}
        {item.address && <p>{item.address}</p>}
        {typeof item.opening_hours === "string" && <p>{item.opening_hours}</p>}
      </div>
      {!restaurant && (
        <div className="stay-price">
          <strong>{formatPrice(item.nightly_price, item.currency)}</strong>
          {typeof item.nightly_price === "number" && <small>Per night</small>}
        </div>
      )}
      <div className="result-links">
        {restaurant ? <>
          <ExternalLink url={item.website}>Visit website</ExternalLink>
          <ExternalLink url={item.maps_link}>Open Maps</ExternalLink>
        </> : item.property_website || item.link
          ? <ExternalLink url={item.property_website || item.link}>Visit property site</ExternalLink>
          : <ExternalLink url={item.search_link}>Search hotel</ExternalLink>}
      </div>
    </article>
  );
}

const tabs = [
  ["flights", "✈", "Flights"],
  ["hotels", "▰", "Hotels"],
  ["restaurants", "♨", "Restaurants"],
  ["attractions", "📍", "Places to Visit"],
];

const previewCopy = {
  flights: ["Find flights for your route", "Choose your cities and travel dates to compare airlines and departure times."],
  hotels: ["Find a place to stay", "Plan an overnight trip to compare available hotels and nightly rates."],
  restaurants: ["Find places to eat", "Choose a destination to discover nearby restaurants and see them on the map."],
  attractions: ["Find places to visit", "Plan a trip to discover nearby sights and open their locations in Maps."],
};

function AttractionCard({ place, destination, loading, onAsk }) {
  const location = place.address || destination;
  const question = `Tell me about the history of ${place.name}${location ? ` in ${location}` : ""} and what I should know before visiting.`;
  return (
    <article className="stay-card">
      <div className="stay-info">
        <h3>{place.name}</h3>
        {place.address && <p>{place.address}</p>}
        {typeof place.opening_hours === "string" && <p>{place.opening_hours}</p>}
      </div>
      <div className="result-links">
        <ExternalLink url={place.website}>Visit website</ExternalLink>
        <ExternalLink url={place.maps_link}>Open Maps</ExternalLink>
        <button className="book-button" type="button" disabled={loading} onClick={() => onAsk(question)}>
          Ask about this place
        </button>
      </div>
    </article>
  );
}

export default function ResultsPanel({ data, loading, activeTab, setActiveTab, onAsk }) {
  const destination = data.destination;
  const result = data[activeTab];
  const items = result?.[activeTab] || [];
  const displayedItems = activeTab === "hotels"
    ? [...items].sort((first, second) => (first.nightly_price ?? Infinity) - (second.nightly_price ?? Infinity))
    : items;
  return (
    <section className="results-panel" aria-label="Trip results" aria-busy={loading} tabIndex={0}>
      <DestinationHero destination={destination} />

      <div className="tabs" aria-label="Result categories">
        {tabs.map(([id, icon, label]) => (
          <button key={id} type="button" onClick={() => setActiveTab(id)} aria-pressed={activeTab === id} className={activeTab === id ? "selected" : ""}>
            <span aria-hidden="true">{icon}</span>{label}
          </button>
        ))}
      </div>

      {activeTab === "flights" && result ? (
        <>
          <FlightSection leg={result.outbound} title="Outbound flights" currency={result.currency} />
          <FlightSection leg={result.return} title="Return flights" currency={result.currency} />
          {result.warnings?.map((warning) => <p className="disclaimer" key={warning}>{warning}</p>)}
        </>
      ) : (
        <section className="result-section">
          <div className="section-header"><h2>{tabs.find(([id]) => id === activeTab)[2]}</h2></div>
          {displayedItems.length ? displayedItems.map((item, index) => (
            activeTab === "attractions"
              ? <AttractionCard key={index} place={item} destination={result.destination || destination} loading={loading} onAsk={onAsk} />
              : <StayCard key={index} item={item} restaurant={activeTab === "restaurants"} />
          )) : !destination && !loading && result === undefined ? (
            <div className="results-preview">
              {activeTab !== "attractions" && <ResultImage key={activeTab} category={activeTab} />}
              <div>
                <h3>{previewCopy[activeTab][0]}</h3>
                <p>{previewCopy[activeTab][1]}</p>
              </div>
            </div>
          ) : <EmptyResults label={activeTab === "attractions" ? "places to visit" : activeTab} result={result} loading={loading} />}
        </section>
      )}

      {result && <p className="disclaimer">{activeTab === "attractions"
        ? "Check current opening hours and entry fees before visiting."
        : "Verify prices and availability with the provider before booking."}</p>}
    </section>
  );
}
