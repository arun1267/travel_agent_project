import { useState } from "react";

export const CITIES = [
  "Ahmedabad", "Bengaluru", "Chennai", "Delhi", "Goa", "Hyderabad", "Jaipur",
  "Kochi", "Kolkata", "Mumbai", "Pune", "Udaipur", "Varanasi", "Visakhapatnam"
];

const todayISO = () => new Date().toLocaleDateString("en-CA");

function addDays(iso, n) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

export default function TripForm({ onSubmit }) {
  const start = addDays(todayISO(), 14);
  const [form, setForm] = useState({
    from: "",
    to: "",
    tripType: "round",
    travellers: 2,
    budget: "",
    departure: start,
    returnDate: addDays(start, 3)
  });
  const [errors, setErrors] = useState({});

  const set = (key, value) => setForm((old) => ({ ...old, [key]: value }));
  const round = form.tripType === "round";

  function changeDeparture(value) {
    setForm((old) => ({
      ...old,
      departure: value,
      returnDate: old.returnDate < value ? addDays(value, 1) : old.returnDate
    }));
  }

  function submit(event) {
    event.preventDefault();
    const next = {};
    if (!form.from) next.from = "Choose where you're travelling from";
    if (!form.to) next.to = "Choose your destination";
    if (form.from && form.to && form.from === form.to) next.to = "Destination must differ from the origin";
    if (!form.departure) next.departure = "Pick a departure date";
    if (round && (!form.returnDate || form.returnDate < form.departure)) next.returnDate = "Return must be on/after departure";
    setErrors(next);
    if (Object.keys(next).length) return;
    onSubmit({ ...form, budget: form.budget ? Number(form.budget) : null });
  }

  return (
    <form className="trip-form" onSubmit={submit} noValidate>
      <div className="trip-form-title">🧭 Plan your trip</div>

      <div className="tf-row">
        <label className="tf-field">
          <span>From</span>
          <select value={form.from} onChange={(e) => set("from", e.target.value)} className={errors.from ? "invalid" : ""}>
            <option value="">Select city…</option>
            {CITIES.map((c) => <option key={c}>{c}</option>)}
          </select>
          {errors.from && <em>{errors.from}</em>}
        </label>
        <label className="tf-field">
          <span>To</span>
          <select value={form.to} onChange={(e) => set("to", e.target.value)} className={errors.to ? "invalid" : ""}>
            <option value="">Select city…</option>
            {CITIES.map((c) => <option key={c}>{c}</option>)}
          </select>
          {errors.to && <em>{errors.to}</em>}
        </label>
      </div>

      <div className="tf-field">
        <span>Travel type</span>
        <div className="segmented">
          <button type="button" className={round ? "on" : ""} onClick={() => set("tripType", "round")}>⇄ Round trip</button>
          <button type="button" className={!round ? "on" : ""} onClick={() => set("tripType", "oneway")}>→ One-way</button>
        </div>
      </div>

      <div className="tf-row">
        <div className="tf-field">
          <span>Travellers</span>
          <div className="stepper">
            <button type="button" onClick={() => set("travellers", Math.max(1, form.travellers - 1))} aria-label="Fewer travellers">−</button>
            <strong>{form.travellers}</strong>
            <button type="button" onClick={() => set("travellers", Math.min(9, form.travellers + 1))} aria-label="More travellers">+</button>
          </div>
        </div>
        <label className="tf-field">
          <span>Budget (optional)</span>
          <div className="money">
            <i>₹</i>
            <input type="number" min="0" inputMode="numeric" placeholder="e.g. 30000" value={form.budget} onChange={(e) => set("budget", e.target.value)} />
          </div>
        </label>
      </div>

      <div className="tf-row">
        <label className="tf-field">
          <span>Departure</span>
          <input type="date" min={todayISO()} value={form.departure} onChange={(e) => changeDeparture(e.target.value)} className={errors.departure ? "invalid" : ""} />
          {errors.departure && <em>{errors.departure}</em>}
        </label>
        {round && (
          <label className="tf-field">
            <span>Return</span>
            <input type="date" min={form.departure} value={form.returnDate} onChange={(e) => set("returnDate", e.target.value)} className={errors.returnDate ? "invalid" : ""} />
            {errors.returnDate && <em>{errors.returnDate}</em>}
          </label>
        )}
      </div>

      <button className="plan-button" type="submit">✈ Plan Trip</button>
    </form>
  );
}
