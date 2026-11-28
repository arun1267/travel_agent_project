import { useEffect, useState } from "react";
import { fetchDestinationImage, getDestinationImage } from "../data/destinationImages.js";

const destinations = ["Goa", "Jaipur", "Hyderabad", "Udaipur"];

export default function DestinationHero({ destination }) {
  const [index, setIndex] = useState(0);
  const [paused] = useState(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const [photo, setPhoto] = useState(null);
  const place = destination || destinations[index];
  const knownImage = getDestinationImage(place);

  useEffect(() => {
    if (destination || paused) return;
    const timer = window.setInterval(() => {
      setIndex((current) => (current + 1) % destinations.length);
    }, 5500);
    return () => window.clearInterval(timer);
  }, [destination, paused]);

  useEffect(() => {
    if (knownImage) return;
    let cancelled = false;
    fetchDestinationImage(place).then((url) => {
      if (!cancelled) setPhoto({ place, url });
    });
    return () => { cancelled = true; };
  }, [place, knownImage]);

  const image = knownImage || (photo?.place === place ? photo.url : null);

  return (
    <div className="destination-hero" style={image ? {
      backgroundImage: `linear-gradient(0deg, rgba(7,26,59,.87), rgba(7,26,59,.15)), url("${image}")`
    } : undefined}>
      <div className="hero-content">
        {!destination && <p>Explore India</p>}
        <h1>{place}</h1>
      </div>
    </div>
  );
}
