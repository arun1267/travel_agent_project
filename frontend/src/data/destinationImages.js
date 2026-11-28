const SIZE = "?auto=format&fit=crop&w=1500&q=85";

// Hand-picked photos (these win over Wikipedia). Add your own favourites here anytime.
export const DESTINATION_IMAGES = {
  Goa: "https://images.unsplash.com/photo-1512343879784-a960bf40e7f2" + SIZE
};

// Wikipedia page names, so each city finds the right article.
const WIKI_TITLES = {
  Ahmedabad: "Ahmedabad",
  Bengaluru: "Bengaluru",
  Chennai: "Chennai",
  Delhi: "Delhi",
  Goa: "Goa",
  Hyderabad: "Hyderabad,_India",
  Jaipur: "Jaipur",
  Kochi: "Kochi",
  Kolkata: "Kolkata",
  Mumbai: "Mumbai",
  Pune: "Pune",
  Udaipur: "Udaipur",
  Varanasi: "Varanasi",
  Visakhapatnam: "Visakhapatnam"
};

const cache = {};

// Looks up the main photo of the city's Wikipedia page.
export async function fetchDestinationImage(destination) {
  if (!destination) return null;
  if (cache[destination] !== undefined) return cache[destination];

  try {
    const title = encodeURIComponent(WIKI_TITLES[destination] || destination);
    const url =
      "https://en.wikipedia.org/w/api.php?action=query&prop=pageimages&piprop=thumbnail" +
      `&pithumbsize=1500&redirects=1&format=json&origin=*&titles=${title}`;
    const response = await fetch(url);
    const json = await response.json();
    const page = Object.values(json?.query?.pages || {})[0];
    cache[destination] = page?.thumbnail?.source || null;
  } catch {
    cache[destination] = null;
  }
  return cache[destination];
}

// Priority: image sent by the backend, then the hand-picked list above.
export function getDestinationImage(destination, apiImage) {
  return apiImage || DESTINATION_IMAGES[destination] || null;
}