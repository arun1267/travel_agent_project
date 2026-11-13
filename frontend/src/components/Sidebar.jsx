const items = [
  ["💬", "Chat Planner", "chat"],
  ["▦", "My Trips", "trips"],
  ["♡", "Favorites", "favorites"],
  ["⚙", "Settings", "settings"]
];

export default function Sidebar({ activePage, onNavigate }) {
  return (
    <aside className="sidebar">
      <div className="brand">
        
        <img className="brand-logo" src="/image.png" alt="Travelling Trev" />
        
      </div>

      <nav className="nav-list">
        {items.map(([icon, label, id]) => (
          <button
            key={id}
            className={`nav-item ${activePage === id ? "active" : ""}`}
            onClick={() => onNavigate(id)}
            type="button"
          >
            <span className="nav-icon">{icon}</span>
            {label}
          </button>
        ))}
      </nav>

      <div className="sidebar-footer">
        <div className="india-art">🏛️</div>
        <strong>Explore India<br />Your Way</strong>
        <div className="footer-swoosh" />
      </div>
    </aside>
  );
}
