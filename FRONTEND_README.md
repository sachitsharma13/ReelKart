# Hamilton Theatre — Web Frontend

A single self-contained `index.html` that ports the v2 desktop GUI's dark
cinema design to the web, wired to your FastAPI backend (`main.py`).

## What's implemented (mirrors the v2 GUI improvement list)

- **Design system** — same tokens as `trial.py`'s `C`/`F` dicts (deep navy
  `#08080F` base, crimson `#E03A50` accent, emerald price/green success,
  gold tier badges), defined once as CSS variables so re-theming is a
  one-line change per token.
- **Seat map** — drawn on `<canvas>`, not a grid of `<button>`s. Each seat
  is a two-part headrest + body shape with a seatback ridge behind it,
  colored per state (available / selected / booked) with a hover glow.
  A screen bar with fanning perspective glow lines sits above the seats.
  Section headers show the tier price modifier (`+₹200`, `−₹100`, `Base`)
  as gold badges, exactly like the desktop app.
- **Layout** — 4px crimson identity stripe across the top, header with
  theatre name + "My Booking" / "Admin" buttons, seat map on the left,
  a scrollable stacked-card sidebar on the right (ticket type, selected
  seats, customer form, live total, Book Now).
- **My Booking** — modal lookup by Customer ID, shows details, links to
  the HTML receipt, lets a customer cancel their own booking.
- **Admin panel** — dark login modal (not a browser `prompt`), JWT stored
  in memory for the session, searchable/filterable bookings table with
  Enter-to-search on both fields, row selection + cancel.
- **Receipts** — opens the backend's existing dark BMS-style HTML receipt
  in a new tab; no need to duplicate that design in the frontend.

## Running it

1. Start your FastAPI backend (`uvicorn main:app --reload --port 8000`).
2. Open `index.html` directly in a browser, or serve it with any static
   file server.
3. By default it talks to `http://localhost:8000`. To point it at a
   deployed backend (e.g. Railway) without editing the file, open it as:

   ```
   index.html?api=https://your-backend.up.railway.app
   ```

   The chosen API base is remembered in `localStorage` after that.

## Notes / things you may want to adjust next

- CORS on the backend is currently `allow_origins=["*"]` — fine for
  development, but tighten it to your deployed frontend's origin before
  going live.
- The admin JWT is kept only in a JS variable (not persisted), so the
  admin panel needs a fresh login after a page refresh. Easy to change
  to `sessionStorage` if you'd rather it survive a reload.
- Pricing shown in the sidebar is computed client-side using the same
  formula as the backend (`base + tier modifier, floor ₹100`) purely for
  the live total preview — the backend always recalculates and is the
  source of truth at booking time.
