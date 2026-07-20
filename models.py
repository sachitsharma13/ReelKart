# ==============================================================================
# models.py  —  ReelKart Theatres  |  Pydantic schemas
# FastAPI uses these for automatic request validation + OpenAPI docs.
# ==============================================================================

from pydantic import BaseModel, field_validator
import re


# ── User accounts ────────────────────────────────────────────────────────────

class UserRegisterRequest(BaseModel):
    email:      str
    password:   str
    first_name: str
    last_name:  str
    phone:      str
    sex:        str

    @field_validator("email")
    @classmethod
    def email_valid(cls, v):
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
            raise ValueError("Invalid email address.")
        return v.lower()

    @field_validator("phone")
    @classmethod
    def phone_valid(cls, v):
        if not re.match(r"^[\d\s\+\-\(\)]{7,15}$", v):
            raise ValueError("Invalid phone number format.")
        return v

    @field_validator("password")
    @classmethod
    def password_valid(cls, v):
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters.")
        return v


class UserLoginRequest(BaseModel):
    email:    str
    password: str


class UserPublic(BaseModel):
    id:         int
    email:      str
    first_name: str
    last_name:  str
    phone:      str
    sex:        str
    role:       str


class AuthResponse(BaseModel):
    access_token: str
    token_type:   str = "bearer"
    user:         UserPublic


# ── Request bodies ─────────────────────────────────────────────────────────────

class AddonSelection(BaseModel):
    item_id: str
    qty: int


class BookingRequest(BaseModel):
    payment_mode: str
    show_id:      int
    ticket_type:  str
    seats:        list[str]
    addons:       list[AddonSelection] = []
    promo_code:   str | None = None

    @field_validator("seats")
    @classmethod
    def seats_not_empty(cls, v):
        if not v:
            raise ValueError("At least one seat must be selected.")
        return v

    @field_validator("ticket_type")
    @classmethod
    def ticket_type_valid(cls, v):
        if v not in ("4DX", "IMAX", "Silver"):
            raise ValueError("ticket_type must be one of: 4DX, IMAX, Silver.")
        return v


# ── Promo codes ──────────────────────────────────────────────────────────────────

class PromoValidateRequest(BaseModel):
    code:        str
    ticket_type: str
    seats:       list[str]
    addons:      list[AddonSelection] = []


class PromoValidateResponse(BaseModel):
    valid:           bool
    code:            str | None = None
    discount_amount: int
    message:         str


# ── Food & Beverage add-ons ─────────────────────────────────────────────────────

class AddonCatalogItem(BaseModel):
    item_id: str
    name:    str
    price:   int
    icon:    str


class AddonLineItem(BaseModel):
    item_id:    str
    name:       str
    icon:       str
    price:      int
    qty:        int
    line_total: int


# ── Showtimes / cinemas ─────────────────────────────────────────────────────────

class CityCatalog(BaseModel):
    popular:             list[str]
    other:               list[str]
    cities_with_cinemas: list[str]


class ShowSlot(BaseModel):
    show_id:    int
    show_time:  str
    format:     str | None = None
    status:     str          # "available" | "almost" | "full"


class CinemaShowtimes(BaseModel):
    cinema_id:  str
    name:       str
    location:   str
    amenities:  list[str]
    shows:      list[ShowSlot]


class ShowtimesResponse(BaseModel):
    movie:      str
    date:       str
    cinemas:    list[CinemaShowtimes]


class ShowDetailResponse(BaseModel):
    show_id:          int
    movie:            str
    show_date:        str
    show_time:        str
    format:           str | None = None
    cinema_id:        str
    cinema_name:      str
    cinema_location:  str


# ── Response shapes ────────────────────────────────────────────────────────────

class SeatMapResponse(BaseModel):
    show_id:      int
    movie:        str
    cinema_name:  str
    show_date:    str
    show_time:    str
    booked_seats: list[str]
    held_seats:   list[str] = []   # temporarily reserved by someone (mid-checkout)
    layout: list[dict]          # section metadata for the frontend to render


class SeatHoldRequest(BaseModel):
    show_id: int
    seats:   list[str]


class SeatHoldResponse(BaseModel):
    held:            list[str]   # seats this request successfully reserved
    rejected:        list[str]   # seats already held/booked by someone else
    hold_minutes:    int
    held_until:      str          # ISO timestamp — for the frontend's countdown


class SeatReleaseRequest(BaseModel):
    show_id: int
    seats:   list[str]


class BookingResponse(BaseModel):
    customer_id:  str
    show_id:      int
    movie:        str
    cinema_name:  str
    show_date:    str
    show_time:    str
    ticket_type:  str
    seats:        list[str]
    subtotal:      int          # seat subtotal only
    addon_subtotal: int = 0
    addons:        list[AddonLineItem] = []
    promo_code:    str | None = None
    discount_amount: int = 0
    tax_amount:   int
    total_amount: int
    receipt_url:  str


class BookingDetailResponse(BaseModel):
    customer_id:  str
    show_id:      int
    movie:        str
    cinema_name:  str
    show_date:    str
    show_time:    str
    format:       str | None = None
    ticket_type:  str
    seats:        list[str]
    subtotal:      int          # seat subtotal only
    addon_subtotal: int = 0
    addons:        list[AddonLineItem] = []
    promo_code:    str | None = None
    discount_amount: int = 0
    tax_amount:   int
    total_amount: int
    booked_on:    str
    first_name:   str
    last_name:    str
    phone:        str
    email:        str
    sex:          str
    payment_mode: str


class AdminBookingRow(BaseModel):
    customer_id:  str
    movie:        str
    cinema_name:  str
    show_date:    str
    show_time:    str
    ticket_type:  str
    seats:        list[str]
    total_amount: int
    booked_on:    str


class BookingSummary(BaseModel):
    customer_id:  str
    movie:        str
    cinema_name:  str
    show_date:    str
    show_time:    str
    ticket_type:  str
    seats:        list[str]
    total_amount: int
    booked_on:    str


class MessageResponse(BaseModel):
    message: str


# ── Admin analytics ──────────────────────────────────────────────────────────────

class DailyStat(BaseModel):
    date:     str
    revenue:  int
    bookings: int


class TicketTypeStat(BaseModel):
    ticket_type: str
    count:       int


class PopularMovieStat(BaseModel):
    movie:   str
    tickets: int


class CinemaRevenueStat(BaseModel):
    cinema_name: str
    revenue:     int


class AnalyticsResponse(BaseModel):
    total_revenue:        int
    total_bookings:       int
    tickets_sold:         int
    avg_occupancy_pct:    float
    daily_series:         list[DailyStat]
    ticket_type_breakdown: list[TicketTypeStat]
    popular_movies:        list[PopularMovieStat]
    cinema_breakdown:      list[CinemaRevenueStat]
