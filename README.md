# Hamilton Theatre — FastAPI Backend

## Local Setup (Day 1)

```bash
# 1. Clone / navigate into the backend folder
cd hamilton_backend

# 2. Create a virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set environment variables
cp .env.example .env
# Edit .env with your MySQL password and desired admin credentials

# 5. Load the .env file and start the server
export $(cat .env | xargs)      # Linux / Mac
# Windows (PowerShell): Get-Content .env | ForEach-Object { $env:$_ }

uvicorn main:app --reload --port 8000
```

The API is now live at **http://localhost:8000**

Interactive docs (auto-generated): **http://localhost:8000/docs**

---

## API Routes Quick Reference

| Method | Route | Auth | Description |
|--------|-------|------|-------------|
| GET | `/` | None | Health check |
| GET | `/shows` | None | All show times, movies, prices |
| GET | `/seats?movie=&show_time=` | None | Booked seats + layout |
| POST | `/book` | None | Create a booking |
| GET | `/booking/{id}` | None | View a booking |
| DELETE | `/booking/{id}` | None | Cancel a booking |
| GET | `/receipt/{id}` | None | View HTML receipt |
| POST | `/admin/login` | None | Get JWT token |
| GET | `/admin/bookings` | JWT | Search all bookings |
| DELETE | `/admin/booking/{id}` | JWT | Admin cancel |

---

## Admin API Usage

```bash
# 1. Login and get a token
curl -X POST http://localhost:8000/admin/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "your_password"}'

# Response: {"access_token": "eyJ...", "token_type": "bearer"}

# 2. Use the token in subsequent requests
curl http://localhost:8000/admin/bookings \
  -H "Authorization: Bearer eyJ..."

# Search by movie
curl "http://localhost:8000/admin/bookings?movie=Avengers" \
  -H "Authorization: Bearer eyJ..."
```

---

## Deploying to Railway (Free Tier)

1. Push this folder to a GitHub repository
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Add a MySQL plugin (Railway provisions it automatically)
4. Set environment variables in Railway's dashboard (same as your .env)
5. Railway auto-detects the `requirements.txt` and runs `uvicorn main:app`
6. Your API gets a public URL like `https://hamilton-theatre.up.railway.app`

---

## File Structure

```
hamilton_backend/
├── main.py           ← FastAPI app + all routes
├── db.py             ← Database helpers (ported from tkinter app)
├── models.py         ← Pydantic request / response schemas
├── receipt.py        ← HTML receipt generator
├── requirements.txt
├── .env.example      ← Copy to .env and fill in credentials
└── receipts/         ← Generated HTML receipts (auto-created)
```

---

## Connecting Your Frontend

Point your HTML seat map's fetch calls to this backend:

```javascript
// Get seat availability
const res  = await fetch(`http://localhost:8000/seats?movie=Senna&show_time=Morning (9:00 AM)`);
const data = await res.json();
// data.booked_seats → ["A1", "B3", ...]
// data.layout       → section/row/col metadata to draw the seat map

// Create a booking
const booking = await fetch("http://localhost:8000/book", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    first_name:   "Sachit",
    last_name:    "...",
    phone:        "9876543210",
    email:        "sachit@example.com",
    sex:          "M",
    payment_mode: "UPI",
    movie:        "Senna",
    show_time:    "Morning (9:00 AM)",
    ticket_type:  "IMAX",
    seats:        ["C3", "C4"],
  }),
});
const result = await booking.json();
// result.customer_id  → "A1B2C3D4"
// result.receipt_url  → "/receipt/A1B2C3D4"
```
