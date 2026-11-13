# TravelBuddy Frontend (fixed starter)

This is a minimal React + Vite frontend. It uses plain CSS (no Tailwind setup required) to avoid styling/configuration issues.

## Run it

1. Open a terminal in this `frontend` folder.
2. Remove old installed packages if you are replacing the previous frontend:
   - Windows PowerShell: `Remove-Item -Recurse -Force node_modules` (if it exists)
   - Also delete `package-lock.json` (if it exists)
3. Run:

```bash
npm install
npm run dev
```

4. Open the exact Local URL printed by Vite, normally `http://localhost:5173/`.

For a production build:

```bash
npm run build
```

## Notes
- The screen initially displays demo Goa flight, hotel and restaurant cards.
- The chat uses a local demo response so the UI works without FastAPI.
- Backend integration is isolated in `src/services/chatService.js`.
- Do not put SerpAPI, Geoapify or AWS secrets in frontend environment variables.
