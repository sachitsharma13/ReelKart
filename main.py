# ==============================================================================
# main.py  —  ReelKart Theatres  |  FastAPI Backend
# v4: user accounts (admin is a seeded account with role='admin')
#
# Routes
# ──────
#   GET  /                            health check
#   POST /auth/register               create an account, returns a JWT
#   POST /auth/login                  log in (customers AND admin), returns a JWT
#   GET  /auth/me                     current account profile (JWT required)
#   GET  /my-bookings                 logged-in account's bookings (JWT required)
#   GET  /movies                      master movie catalog
#   GET  /addons                      F&B add-on catalog
#   GET  /showtimes?movie=&date=      cinemas + showtimes for a movie/date
#   GET  /show/{show_id}              details for a single showtime
#   GET  /seats?show_id=              booked seats + layout for seat map
#   POST /book                        create a booking (JWT required) → receipt URL
#   GET  /booking/{customer_id}       look up a booking
#   DELETE /booking/{customer_id}     cancel a booking (JWT required — owner or admin)
#   GET  /admin/bookings              search all bookings (admin JWT required)
#   DELETE /admin/booking/{cid}       cancel any booking (admin JWT required)
#   GET  /receipt/{customer_id}       serve the HTML receipt file
#
# Run locally:
#   pip install -r requirements.txt
#   uvicorn main:app --reload --port 8000
# ==============================================================================

import logging
import datetime
from datetime import timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Depends, Query, status, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from fastapi.security import OAuth2PasswordBearer
from fastapi.concurrency import run_in_threadpool

from jose import JWTError, jwt
import mysql.connector
import bcrypt

from config import settings
import db
from db import (
    ensure_tables, build_seat_section_map, load_booked_seats,
    commit_booking, cancel_booking, get_booking,
    admin_search_bookings, generate_customer_id,
    price_for_seat, compute_totals, split_stored_total, validate_promo_code,
    resolve_addons, list_addons, list_cities, get_analytics,
    validate_email, validate_phone,
    receipt_path_for, SEAT_LAYOUT,
    BASE_PRICES, SEAT_TIER_MOD, TAX_RATE, MIN_SEAT_PRICE,
    list_movies, get_showtimes, get_show,
    SHOW_WINDOW_DAYS,
    create_user, get_user_by_email, get_user_by_id,
    seed_admin_user, list_bookings_for_user,
    hold_seats, release_seats, get_held_seats, SEAT_HOLD_MINUTES,
)
from models import (
    BookingRequest, BookingResponse, BookingDetailResponse,
    SeatMapResponse, AdminBookingRow, BookingSummary,
    MessageResponse,
    ShowtimesResponse, ShowDetailResponse, AddonCatalogItem, CityCatalog,
    UserRegisterRequest, UserLoginRequest, UserPublic, AuthResponse,
    AnalyticsResponse, PromoValidateRequest, PromoValidateResponse,
    SeatHoldRequest, SeatHoldResponse, SeatReleaseRequest,
)
from receipt import generate_receipt, render_receipt_pdf
from email_service import send_booking_confirmation_email

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ── Admin account bootstrapping (from config.py, which loads .env automatically)
# The admin account is just a regular row in `users` with role='admin', seeded
# once at startup — there is no separate admin login system anymore. Everyone,
# including the admin, authenticates through POST /auth/login.
ADMIN_USERNAME      = settings.ADMIN_USERNAME
ADMIN_PASSWORD_HASH = settings.ADMIN_PASSWORD_HASH   # bcrypt hash — preferred
ADMIN_PASSWORD       = settings.ADMIN_PASSWORD       # plaintext — legacy fallback only

if not ADMIN_PASSWORD_HASH and not ADMIN_PASSWORD:
    ADMIN_PASSWORD = "admin123"   # local-dev default so the app still boots

if not ADMIN_PASSWORD_HASH:
    logger.warning(
        "ADMIN_PASSWORD_HASH is not set — falling back to plaintext ADMIN_PASSWORD "
        "(hashed at startup before it's stored, but generate a real bcrypt hash "
        "before deploying): python -c \"import bcrypt; "
        "print(bcrypt.hashpw(b'yourpassword', bcrypt.gensalt()).decode())\" "
        "and set it as ADMIN_PASSWORD_HASH instead."
    )
    _resolved_admin_hash = bcrypt.hashpw(ADMIN_PASSWORD.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
else:
    _resolved_admin_hash = ADMIN_PASSWORD_HASH


# ── JWT config  (from config.py — change JWT_SECRET in production!) ────────────
SECRET_KEY       = settings.JWT_SECRET
ALGORITHM        = settings.JWT_ALGORITHM
TOKEN_EXPIRE_MIN = settings.TOKEN_EXPIRE_MINUTES

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="ReelKart Theatres API",
    description="Backend for the ReelKart Theatres ticketing system.",
    version="5.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,   # "*" by default; set CORS_ORIGINS in .env to restrict
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Live seat updates (WebSocket) ───────────────────────────────────────────────
# In-memory per-process connection registry, grouped by show_id. This is fine
# for a single-worker `uvicorn` process (the normal way this app runs); a
# multi-worker/multi-instance deployment would need a shared pub/sub layer
# (e.g. Redis) instead, since each worker would otherwise have its own
# disconnected set of connections.
class SeatUpdateManager:
    def __init__(self):
        self.active: dict[int, set[WebSocket]] = {}

    async def connect(self, show_id: int, websocket: WebSocket):
        await websocket.accept()
        self.active.setdefault(show_id, set()).add(websocket)

    def disconnect(self, show_id: int, websocket: WebSocket):
        conns = self.active.get(show_id)
        if conns:
            conns.discard(websocket)
            if not conns:
                self.active.pop(show_id, None)

    async def broadcast(self, show_id: int, message: dict):
        conns = list(self.active.get(show_id, ()))
        for ws in conns:
            try:
                await ws.send_json(message)
            except Exception:
                self.disconnect(show_id, ws)


seat_updates = SeatUpdateManager()


@app.websocket("/ws/seats/{show_id}")
async def seat_updates_ws(websocket: WebSocket, show_id: int):
    """Clients viewing a show's seat map connect here to get instant push
    updates ({"type": "seats_booked"|"seats_released", "seats": [...]})
    whenever anyone else books or cancels seats for the same show, instead
    of waiting on a polling interval.
    """
    await seat_updates.connect(show_id, websocket)
    try:
        while True:
            # We don't need anything from the client — just keep the socket
            # open and detect disconnects via the exception this raises.
            await websocket.receive_text()
    except WebSocketDisconnect:
        seat_updates.disconnect(show_id, websocket)
    except Exception:
        seat_updates.disconnect(show_id, websocket)


# ── Startup ────────────────────────────────────────────────────────────────────
@app.on_event("startup")
def startup():
    try:
        ensure_tables()
        build_seat_section_map()
        seed_admin_user(ADMIN_USERNAME, _resolved_admin_hash)
        logger.info("Database ready. Seat section map built. Cinemas/showtimes/admin seeded.")
    except Exception as e:
        logger.error("Startup DB error: %s", e)


# ── JWT / auth helpers ───────────────────────────────────────────────────────────
def create_access_token(user_id: int) -> str:
    expire = datetime.datetime.now(timezone.utc) + timedelta(minutes=TOKEN_EXPIRE_MIN)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _public_user(user: dict) -> UserPublic:
    return UserPublic(
        id=user["id"], email=user["email"], first_name=user["first_name"],
        last_name=user["last_name"], phone=user["phone"], sex=user["sex"], role=user["role"],
    )


def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    """Dependency — raises 401 if the JWT is missing, invalid, or the account
    it points to no longer exists. Returns the full user dict (includes role).
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Please log in.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub"))
    except (JWTError, ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account not found. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def get_current_admin(user: dict = Depends(get_current_user)) -> dict:
    """Dependency — same as get_current_user, but also requires role='admin'."""
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return user


# ==============================================================================
# PUBLIC ROUTES
# ==============================================================================

@app.get("/", tags=["Health"])
def health_check():
    return {"status": "ok", "service": "ReelKart Theatres API"}


# ==============================================================================
# ACCOUNTS
# ==============================================================================

@app.post("/auth/register", response_model=AuthResponse, status_code=201, tags=["Accounts"])
async def register(body: UserRegisterRequest):
    """Create a new customer account and log them in immediately."""
    existing = await run_in_threadpool(get_user_by_email, body.email)
    if existing:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    password_hash = bcrypt.hashpw(body.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    try:
        user = await run_in_threadpool(
            create_user,
            body.email, password_hash, body.first_name, body.last_name, body.phone, body.sex,
            "customer",
        )
    except mysql.connector.errors.IntegrityError:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    except Exception as e:
        logger.error("create_user error: %s", e)
        raise HTTPException(status_code=500, detail="Could not create account.")

    token = create_access_token(user["id"])
    return AuthResponse(access_token=token, user=_public_user(user))


@app.post("/auth/login", response_model=AuthResponse, tags=["Accounts"])
async def login(body: UserLoginRequest):
    """
    Log in with email + password. This is the single login for both regular
    customers and the admin account — the admin is just a user row with
    role='admin', seeded at startup.
    """
    user = await run_in_threadpool(get_user_by_email, body.email.lower().strip())
    if not user or not bcrypt.checkpw(body.password.encode("utf-8"), user["password_hash"].encode("utf-8")):
        raise HTTPException(status_code=401, detail="Incorrect email or password.")

    token = create_access_token(user["id"])
    return AuthResponse(access_token=token, user=_public_user(user))


@app.get("/auth/me", response_model=UserPublic, tags=["Accounts"])
async def whoami(user: dict = Depends(get_current_user)):
    """Returns the logged-in account's profile — used to restore a session
    after a page reload from a stored token.
    """
    return _public_user(user)


@app.get("/my-bookings", response_model=list[BookingSummary], tags=["Accounts"])
async def my_bookings(user: dict = Depends(get_current_user)):
    """All bookings made by the logged-in account, most recent first."""
    try:
        rows = await run_in_threadpool(list_bookings_for_user, user["id"])
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB error: {e}")
    return [BookingSummary(**{**r, "show_date": str(r["show_date"])}) for r in rows]


# ── Movies ─────────────────────────────────────────────────────────────────────
@app.get("/movies", tags=["Catalog"])
def list_all_movies():
    """
    Master movie catalog + the booking window (today .. today+N-1),
    used to populate the 'Now Showing' picker and the date strip.
    """
    today = datetime.date.today()
    dates = [
        (today + datetime.timedelta(days=i)).isoformat()
        for i in range(SHOW_WINDOW_DAYS)
    ]
    return {
        "movies": list_movies(),
        "dates": dates,
        "ticket_types": [
            {"name": t, "base_price": p}
            for t, p in BASE_PRICES.items()
        ],
        "seat_tiers": [
            {"section": s, "modifier": m}
            for s, m in SEAT_TIER_MOD.items()
        ],
        "tax_rate": TAX_RATE,
        "min_seat_price": MIN_SEAT_PRICE,
    }


# ── Food & Beverage catalog ─────────────────────────────────────────────────────
@app.get("/addons", response_model=list[AddonCatalogItem], tags=["Catalog"])
async def addons_catalog():
    """The Food & Beverage add-on menu, offered alongside seat selection."""
    return [AddonCatalogItem(**a) for a in list_addons()]


# ── Promo codes ──────────────────────────────────────────────────────────────────
@app.post("/promos/validate", response_model=PromoValidateResponse, tags=["Catalog"])
async def promos_validate(body: PromoValidateRequest):
    """Check a promo code against the current cart (seats + add-ons) and
    return the discount it would apply — used to preview the discount
    before actually booking.
    """
    addon_dicts = [a.model_dump() for a in body.addons]
    seat_subtotal, addon_subtotal, _discount, _tax, _total = await run_in_threadpool(
        compute_totals, body.ticket_type, body.seats, addon_dicts, None
    )
    pretax = seat_subtotal + addon_subtotal
    result = await run_in_threadpool(validate_promo_code, body.code, pretax)
    return PromoValidateResponse(**result)


# ── Cities ─────────────────────────────────────────────────────────────────────
@app.get("/cities", response_model=CityCatalog, tags=["Catalog"])
async def cities_catalog():
    """City picker data — popular grid, broader list, and which cities
    actually have a ReelKart Theatres branch.
    """
    return CityCatalog(**list_cities())


# ── Showtimes (by movie + date, grouped by cinema) ─────────────────────────────
@app.get("/showtimes", response_model=ShowtimesResponse, tags=["Catalog"])
async def showtimes(
    movie: str = Query(..., description="Exact movie name"),
    date:  str = Query(..., description="Date in YYYY-MM-DD format"),
    city:  str = Query("", description="Optional city filter, e.g. 'Pune'"),
):
    """
    Returns every cinema branch (optionally scoped to one city) showing this
    movie on this date, each with its list of showtimes + live fill status.
    Powers the 'showtimes by cinema' listing page.
    """
    try:
        cinemas = await run_in_threadpool(get_showtimes, movie, date, city or None)
    except Exception as e:
        logger.error("get_showtimes error: %s", e)
        raise HTTPException(status_code=503, detail="Could not reach database.")

    return ShowtimesResponse(movie=movie, date=date, cinemas=cinemas)


@app.get("/show/{show_id}", response_model=ShowDetailResponse, tags=["Catalog"])
async def show_detail(show_id: int):
    """Details for a single showtime instance (cinema + movie + date/time/format)."""
    show = await run_in_threadpool(get_show, show_id)
    if not show:
        raise HTTPException(status_code=404, detail="Show not found.")
    return ShowDetailResponse(**show)


# ── Seat map ───────────────────────────────────────────────────────────────────
@app.get("/seats", response_model=SeatMapResponse, tags=["Seats"])
async def get_seat_map(show_id: int = Query(..., description="Show instance ID")):
    """
    Returns which seats are already booked for a given show instance,
    plus the full layout definition so the frontend can draw the seat map.
    """
    show = await run_in_threadpool(get_show, show_id)
    if not show:
        raise HTTPException(status_code=404, detail="Show not found.")

    try:
        booked = await run_in_threadpool(load_booked_seats, show_id)
        held = await run_in_threadpool(get_held_seats, show_id)
    except Exception as e:
        logger.error("load_booked_seats error: %s", e)
        raise HTTPException(status_code=503, detail="Could not reach database.")

    layout = [
        {
            "section":     section_name,
            "rows":        row_labels,
            "cols":        n_cols,
            "aisles_after": aisles,
        }
        for section_name, row_labels, n_cols, aisles in SEAT_LAYOUT
    ]

    return SeatMapResponse(
        show_id=show_id,
        movie=show["movie"],
        cinema_name=show["cinema_name"],
        show_date=show["show_date"],
        show_time=show["show_time"],
        booked_seats=sorted(booked),
        held_seats=sorted(held - booked),   # a booked seat is never also shown as merely "held"
        layout=layout,
    )


# ── Seat holds (temporary reservation while checking out) ──────────────────────
@app.post("/seats/hold", response_model=SeatHoldResponse, tags=["Seats"])
async def hold_seats_route(body: SeatHoldRequest):
    """
    Reserve the given seats for SEAT_HOLD_MINUTES so two people can't both
    think they've got the same seat while one of them is mid-checkout.
    Seats already held by someone else (or booked) come back in `rejected`.
    """
    show = await run_in_threadpool(get_show, body.show_id)
    if not show:
        raise HTTPException(status_code=404, detail="Show not found.")

    try:
        already_booked = await run_in_threadpool(load_booked_seats, body.show_id)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB error: {e}")

    # Don't even attempt a hold on seats that are already permanently booked.
    requestable = [s for s in body.seats if s not in already_booked]
    already_booked_requested = [s for s in body.seats if s in already_booked]

    try:
        held, rejected = await run_in_threadpool(hold_seats, body.show_id, requestable)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB error: {e}")

    rejected = rejected + already_booked_requested

    if held:
        await seat_updates.broadcast(body.show_id, {"type": "seats_held", "seats": held})

    held_until = datetime.datetime.now(timezone.utc) + timedelta(minutes=SEAT_HOLD_MINUTES)
    return SeatHoldResponse(
        held=held, rejected=rejected,
        hold_minutes=SEAT_HOLD_MINUTES, held_until=held_until.isoformat(),
    )


@app.post("/seats/release", response_model=MessageResponse, tags=["Seats"])
async def release_seats_route(body: SeatReleaseRequest):
    """Release a hold early (e.g. the person deselected a seat or left the
    checkout flow) — frees it up for others immediately instead of waiting
    for the hold to expire naturally.
    """
    try:
        await run_in_threadpool(release_seats, body.show_id, body.seats)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB error: {e}")

    if body.seats:
        await seat_updates.broadcast(body.show_id, {"type": "seats_released", "seats": body.seats})
    return MessageResponse(message="Released.")


# ── Book ───────────────────────────────────────────────────────────────────────
@app.post("/book", response_model=BookingResponse, status_code=201, tags=["Booking"])
async def book_seats(payload: BookingRequest, user: dict = Depends(get_current_user)):
    """
    Create a new booking for the logged-in account.
    - Validates the request (Pydantic handles field types + custom validators).
    - Checks for seat conflicts immediately before writing.
    - Saves to DB and generates the HTML receipt.
    """
    show = await run_in_threadpool(get_show, payload.show_id)
    if not show:
        raise HTTPException(status_code=404, detail="Show not found.")

    # Re-check seat availability right before committing (race-condition guard)
    try:
        already_booked = await run_in_threadpool(load_booked_seats, payload.show_id)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB error: {e}")

    conflict = set(payload.seats) & already_booked
    if conflict:
        raise HTTPException(
            status_code=409,
            detail=f"Seats already taken: {', '.join(sorted(conflict))}. Please re-select.",
        )

    # Resolve + validate the requested add-ons against the catalog
    addon_dicts = [a.model_dump() for a in payload.addons]
    resolved_addons = resolve_addons(addon_dicts)

    # Calculate total — seat subtotal + add-ons, minus any promo discount,
    # plus tax, is what actually gets charged
    subtotal, addon_subtotal, discount_amount, tax_amount, total = compute_totals(
        payload.ticket_type, payload.seats, addon_dicts, payload.promo_code
    )
    applied_promo_code = payload.promo_code.upper().strip() if (payload.promo_code and discount_amount > 0) else None

    customer_id = generate_customer_id()
    # Contact info is snapshotted from the logged-in account, not re-typed each time.
    cust_info = {
        "first_name": user["first_name"], "last_name": user["last_name"],
        "phone": user["phone"], "email": user["email"], "sex": user["sex"],
        "payment_mode": payload.payment_mode,
    }

    try:
        await run_in_threadpool(
            commit_booking,
            customer_id, payload.show_id, cust_info,
            payload.ticket_type, payload.seats, total, resolved_addons, user["id"],
            applied_promo_code, discount_amount,
        )
    except mysql.connector.errors.IntegrityError:
        # A parallel request grabbed one of the seats between our check and INSERT
        raise HTTPException(
            status_code=409,
            detail="One or more seats were just taken. Please re-select.",
        )
    except Exception as e:
        logger.error("commit_booking error: %s", e)
        raise HTTPException(status_code=500, detail=f"Could not save booking: {e}")

    # Push the newly-booked seats to anyone else currently viewing this show's
    # seat map, so they see it disappear instantly instead of on their next poll.
    await seat_updates.broadcast(payload.show_id, {"type": "seats_booked", "seats": payload.seats})
    await run_in_threadpool(release_seats, payload.show_id, payload.seats)   # hold is now moot — it's a real booking

    # Generate receipt
    rpath = receipt_path_for(customer_id)
    receipt_info = {
        **cust_info, "seats": payload.seats,
        "movie": show["movie"], "cinema_name": show["cinema_name"],
        "show_date": show["show_date"], "show_time": show["show_time"],
        "format": show["format"],
        "subtotal": subtotal, "addon_subtotal": addon_subtotal,
        "addons": resolved_addons,
        "promo_code": applied_promo_code, "discount_amount": discount_amount,
        "tax_amount": tax_amount, "total_amount": total,
    }
    try:
        await run_in_threadpool(generate_receipt, customer_id, receipt_info, rpath)
    except Exception as e:
        logger.warning("Receipt generation failed: %s", e)

    # Best-effort confirmation email with the PDF ticket attached — a failure
    # here (or no SENDGRID_API_KEY configured) never blocks the booking itself.
    try:
        pdf_bytes = await run_in_threadpool(render_receipt_pdf, customer_id, receipt_info)
        await run_in_threadpool(
            send_booking_confirmation_email, user["email"], customer_id, receipt_info, pdf_bytes,
        )
    except Exception as e:
        logger.warning("Confirmation email step failed for %s: %s", customer_id, e)

    return BookingResponse(
        customer_id  = customer_id,
        show_id      = payload.show_id,
        movie        = show["movie"],
        cinema_name  = show["cinema_name"],
        show_date    = show["show_date"],
        show_time    = show["show_time"],
        ticket_type  = payload.ticket_type,
        seats        = sorted(payload.seats),
        subtotal      = subtotal,
        addon_subtotal = addon_subtotal,
        addons         = resolved_addons,
        promo_code     = applied_promo_code,
        discount_amount = discount_amount,
        tax_amount   = tax_amount,
        total_amount = total,
        receipt_url  = f"/receipt/{customer_id}",
    )


# ── View booking ───────────────────────────────────────────────────────────────
def _reconstruct_receipt_breakdown(booking: dict) -> dict:
    """Rebuild subtotal/tax (and account for any discount) from a stored
    tax-inclusive total, for redisplaying a past booking. Used by every
    endpoint that re-renders a receipt after the fact.
    """
    addon_subtotal = sum(a["line_total"] for a in booking.get("addons", []))
    discount_amount = booking.get("discount_amount", 0) or 0
    discounted_pretax, tax_amount = split_stored_total(booking["total_amount"])
    original_pretax = discounted_pretax + discount_amount
    subtotal = original_pretax - addon_subtotal
    return {"subtotal": subtotal, "addon_subtotal": addon_subtotal, "tax_amount": tax_amount}


@app.get("/booking/{customer_id}",
         response_model=BookingDetailResponse, tags=["Booking"])
async def view_booking(customer_id: str):
    """Retrieve full booking details by Customer ID."""
    try:
        booking = await run_in_threadpool(get_booking, customer_id.upper())
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB error: {e}")

    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found.")

    breakdown = _reconstruct_receipt_breakdown(booking)
    return BookingDetailResponse(**booking, **breakdown)


# ── Cancel booking (customer-facing) ──────────────────────────────────────────
@app.delete("/booking/{customer_id}",
            response_model=MessageResponse, tags=["Booking"])
async def cancel_own_booking(customer_id: str, user: dict = Depends(get_current_user)):
    """Cancel a booking by Customer ID — only the account that made it, or an admin."""
    cid = customer_id.upper()
    try:
        existing = await run_in_threadpool(get_booking, cid)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB error: {e}")

    if not existing:
        raise HTTPException(status_code=404, detail="Booking not found.")

    if existing.get("user_id") != user["id"] and user["role"] != "admin":
        raise HTTPException(status_code=403, detail="You can only cancel your own bookings.")

    try:
        await run_in_threadpool(cancel_booking, cid)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not cancel: {e}")

    await seat_updates.broadcast(existing["show_id"], {"type": "seats_released", "seats": existing["seats"]})

    # Remove receipt file
    rpath = receipt_path_for(cid)
    if rpath.exists():
        rpath.unlink(missing_ok=True)

    return MessageResponse(message=f"Booking {cid} cancelled successfully.")


# ── Receipt file ───────────────────────────────────────────────────────────────
@app.get("/receipt/{customer_id}", response_class=HTMLResponse, tags=["Booking"])
async def get_receipt(customer_id: str):
    """Serve the HTML receipt file directly in the browser."""
    rpath = receipt_path_for(customer_id.upper())
    if not rpath.exists():
        # Try to regenerate from DB
        booking = await run_in_threadpool(get_booking, customer_id.upper())
        if not booking:
            raise HTTPException(status_code=404, detail="Receipt not found.")
        breakdown = _reconstruct_receipt_breakdown(booking)
        generate_receipt(customer_id.upper(), {**booking, **breakdown}, rpath)

    return HTMLResponse(content=rpath.read_text(encoding="utf-8"))


# ── Receipt PDF download ────────────────────────────────────────────────────────
@app.get("/receipt/{customer_id}/pdf", tags=["Booking"])
async def get_receipt_pdf(customer_id: str):
    """Download the receipt as a PDF file (same content as the HTML receipt)."""
    booking = await run_in_threadpool(get_booking, customer_id.upper())
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found.")

    breakdown = _reconstruct_receipt_breakdown(booking)
    receipt_info = {**booking, **breakdown}

    try:
        pdf_bytes = await run_in_threadpool(render_receipt_pdf, customer_id.upper(), receipt_info)
    except Exception as e:
        logger.error("PDF generation failed for %s: %s", customer_id, e)
        raise HTTPException(status_code=500, detail="Could not generate PDF.")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="ReelKart_Ticket_{customer_id.upper()}.pdf"'},
    )


# ==============================================================================
# ADMIN ROUTES  (JWT-protected — requires role='admin', via POST /auth/login)
# ==============================================================================

@app.get("/admin/analytics", response_model=AnalyticsResponse, tags=["Admin"])
async def admin_analytics(
    days: int = Query(14, ge=1, le=90, description="How many days back for the daily trend chart"),
    _admin: dict = Depends(get_current_admin),
):
    """Revenue, tickets sold, occupancy, popular movies, and per-cinema
    breakdown for the admin analytics dashboard.
    """
    try:
        data = await run_in_threadpool(get_analytics, days)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB error: {e}")
    return AnalyticsResponse(**data)


@app.get("/admin/bookings",
         response_model=list[AdminBookingRow], tags=["Admin"])
async def admin_list_bookings(
    movie: str = Query("", description="Partial movie name filter"),
    cid:   str = Query("", description="Exact Customer ID filter"),
    _admin: dict = Depends(get_current_admin),
):
    """
    Search / list all bookings.
    Requires a valid admin JWT (log in via POST /auth/login with the admin account).
    """
    try:
        rows = await run_in_threadpool(admin_search_bookings, movie, cid)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB error: {e}")

    return [AdminBookingRow(**{**r, "show_date": str(r["show_date"])}) for r in rows]


@app.delete("/admin/booking/{customer_id}",
            response_model=MessageResponse, tags=["Admin"])
async def admin_cancel_booking(
    customer_id: str,
    _admin: dict = Depends(get_current_admin),
):
    """Cancel any booking by Customer ID. Requires admin JWT."""
    cid = customer_id.upper()
    try:
        existing = await run_in_threadpool(get_booking, cid)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB error: {e}")

    if not existing:
        raise HTTPException(status_code=404, detail="Booking not found.")

    try:
        await run_in_threadpool(cancel_booking, cid)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not cancel: {e}")

    await seat_updates.broadcast(existing["show_id"], {"type": "seats_released", "seats": existing["seats"]})

    receipt_path_for(cid).unlink(missing_ok=True)
    return MessageResponse(message=f"Booking {cid} cancelled by admin.")
