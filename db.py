# ==============================================================================
# db.py  —  ReelKart Theatres  |  Database helpers
# v3: multi-cinema / multi-showtime data model.
#
# Schema summary
# ──────────────
#   cinemas       one row per branch (ReelKart Theatres — Downtown, etc.)
#   shows         one row per (cinema, movie, date, time, format) instance
#   theatre       one row per customer/booking
#   seats_booked  one row per seat within a booking, FK'd to shows.id
# ==============================================================================

import uuid
import datetime
import logging
import re
import json
from pathlib import Path

import mysql.connector

from config import settings

logger = logging.getLogger(__name__)

# ── Config (from config.py, which loads .env automatically) ───────────────────
DB_CONFIG = {
    "host":     settings.DB_HOST,
    "user":     settings.DB_USER,
    "password": settings.DB_PASSWORD,
    "database": settings.DB_NAME,
}

# ── Business constants ─────────────────────────────────────────────────────────
BASE_PRICES    = {"4DX": 2200, "IMAX": 1200, "Silver": 750}
SEAT_TIER_MOD  = {"Front": 300, "Middle": 0, "Back": -150}
MIN_SEAT_PRICE = 150

# GST-style tax applied on top of the seat subtotal at checkout.
TAX_RATE = 0.20   # 20%

# Food & Beverage add-on catalog — offered alongside seat selection at checkout.
ADDON_CATALOG = [
    {"item_id": "popcorn_reg",   "name": "Popcorn (Regular)",     "price": 180, "icon": "🍿"},
    {"item_id": "popcorn_large", "name": "Popcorn (Large)",       "price": 250, "icon": "🍿"},
    {"item_id": "nachos",        "name": "Nachos & Cheese",       "price": 220, "icon": "🧀"},
    {"item_id": "soft_drink",    "name": "Soft Drink (Large)",    "price": 150, "icon": "🥤"},
    {"item_id": "combo",         "name": "Popcorn + Drink Combo", "price": 380, "icon": "🍿"},
    {"item_id": "choc_bar",      "name": "Chocolate Bar",         "price": 120, "icon": "🍫"},
]
ADDON_BY_ID = {a["item_id"]: a for a in ADDON_CATALOG}

# Promo codes — applied to the seat+addon subtotal before tax.
PROMO_CODES = {
    "FIRST50":   {"type": "percent", "value": 50,  "max_discount": 300,  "description": "50% off (up to ₹300) — welcome offer"},
    "STUDENT20": {"type": "percent", "value": 20,  "max_discount": None, "description": "20% off for students"},
    "FLAT100":   {"type": "flat",    "value": 100, "max_discount": None, "description": "₹100 off your booking"},
    "WEEKEND15": {"type": "percent", "value": 15,  "max_discount": 250,  "description": "15% off (up to ₹250) — weekend special"},
}

# Master movie catalog (used to populate the "Now Showing" picker)
MOVIE_CATALOG = [
    "Ford vs Ferrari", "F1 The Movie", "Senna", "Cars II",
    "Rush", "Avengers Endgame", "Avengers Infinity War", "Days Of Thunder",
    "Happy Gilmore", "The Blind Side", "Ocean's 13", "White House Down",
]

# How many days ahead showtimes are available for booking (today included)
SHOW_WINDOW_DAYS = 7

# Seat layout: (section_name, row_labels, num_cols, aisles_after_col_numbers)
# 250 seats total: Front 4x14=56, Middle 6x16=96, Back 7x14=98
# Shared across every cinema branch.
SEAT_LAYOUT = [
    ("Front",  ["A", "B", "C", "D"],                    14, [7]),
    ("Middle", ["E", "F", "G", "H", "I", "J"],           16, [8]),
    ("Back",   ["K", "L", "M", "N", "O", "P", "Q"],      14, [7]),
]

# Built once at startup — maps every seat label to its section name
SEAT_SECTION_MAP: dict[str, str] = {}

RECEIPTS_DIR = Path("receipts")
RECEIPTS_DIR.mkdir(exist_ok=True)

# ── Cinema branches + their daily showtime template ────────────────────────────
# The same lineup repeats every day within the SHOW_WINDOW_DAYS booking window —
# simple and predictable, and easy to change later without touching booking logic.
CINEMAS = [
    {
        "cinema_id": "downtown",
        "name":      "ReelKart Theatres — Downtown",
        "city":      "Pune",
        "location":  "MG Road, City Centre",
        "amenities": ["Cancellation available", "Contactless entry"],
    },
    {
        "cinema_id": "riverside",
        "name":      "ReelKart Theatres — Riverside Mall",
        "city":      "Pune",
        "location":  "Riverside Mall, Sector 12",
        "amenities": ["Non-cancellable"],
    },
    {
        "cinema_id": "uptown",
        "name":      "ReelKart Theatres — Uptown IMAX",
        "city":      "Pune",
        "location":  "Uptown Boulevard",
        "amenities": ["Cancellation available"],
    },
    {
        "cinema_id": "mumbai_central",
        "name":      "ReelKart Theatres — Mumbai Central",
        "city":      "Mumbai",
        "location":  "Lower Parel",
        "amenities": ["Cancellation available", "Contactless entry"],
    },
    {
        "cinema_id": "bengaluru_mg",
        "name":      "ReelKart Theatres — MG Road",
        "city":      "Bengaluru",
        "location":  "MG Road, Bengaluru",
        "amenities": ["Non-cancellable"],
    },
]

# "Popular cities" grid shown at the top of the picker (BookMyShow-style).
POPULAR_CITIES = [
    "Mumbai", "Delhi-NCR", "Bengaluru", "Hyderabad",
    "Chandigarh", "Ahmedabad", "Pune", "Chennai",
    "Kolkata", "Kochi",
]

# A broader alphabetical list for the "Other cities" section.
OTHER_CITIES = sorted([
    "Agra", "Ajmer", "Amritsar", "Aurangabad", "Bhopal", "Bhubaneswar",
    "Coimbatore", "Dehradun", "Faridabad", "Ghaziabad", "Goa", "Guwahati",
    "Gwalior", "Indore", "Jaipur", "Jalandhar", "Jamshedpur", "Jodhpur",
    "Kanpur", "Kozhikode", "Lucknow", "Ludhiana", "Madurai", "Mangalore",
    "Meerut", "Mysore", "Nagpur", "Nashik", "Noida", "Patna", "Raipur",
    "Rajkot", "Ranchi", "Salem", "Shimla", "Siliguri", "Surat", "Thane",
    "Thiruvananthapuram", "Tiruchirapalli", "Udaipur", "Vadodara",
    "Varanasi", "Vijayawada", "Visakhapatnam", "Warangal",
])


def _slugify_city(city: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", city.lower()).strip("_")


def _generate_remaining_city_branches() -> list[dict]:
    """One ReelKart Theatres branch per city in the picker that doesn't
    already have a hand-crafted one above — so every city in the booking
    system has somewhere to actually show movies.
    """
    already_covered = {c["city"] for c in CINEMAS}
    generated = []
    for city in [*POPULAR_CITIES, *OTHER_CITIES]:
        if city in already_covered:
            continue
        generated.append({
            "cinema_id": _slugify_city(city),
            "name":      f"ReelKart Theatres — {city}",
            "city":      city,
            "location":  f"City Centre, {city}",
            "amenities": ["Cancellation available"],
        })
        already_covered.add(city)
    return generated


CINEMAS = CINEMAS + _generate_remaining_city_branches()

# Six daily showtimes, 9 AM through a 1 AM late show (see db.py's
# _sort_key_for_time — anything before 6 AM is treated as belonging to the
# end of the previous day's lineup, so 1:00 AM correctly lists last).
TIME_SLOTS = ["09:00 AM", "12:00 PM", "03:00 PM", "06:00 PM", "09:00 PM", "01:00 AM"]

# Each cinema's "house format" rotation across its 6 daily slots — gives some
# variety (not every showing of every movie is the same format). Branches
# not explicitly listed here (every generated one) rotate 2D/Atmos.
CINEMA_FORMAT_ROTATION = {
    "downtown":       ["2D", "Atmos", "2D", "Atmos", "INSIGNIA", "2D"],
    "riverside":       ["QSC 7.1", "QSC 7.1", "QSC 7.1", "QSC 7.1", "QSC 7.1", "QSC 7.1"],
    "uptown":          ["2D", "IMAX", "2D", "IMAX", "2D", "IMAX"],
    "mumbai_central":  ["2D", "Atmos", "2D", "Atmos", "INSIGNIA", "2D"],
    "bengaluru_mg":    ["2D", "IMAX", "2D", "IMAX", "2D", "IMAX"],
}
DEFAULT_FORMAT_ROTATION = ["2D", "Atmos", "2D", "Atmos", "2D", "2D"]


def _build_show_template() -> dict[str, list[dict]]:
    """Every cinema shows every movie in the catalog, at all 6 daily time
    slots — so every movie is available in every city, every day.
    """
    template: dict[str, list[dict]] = {}
    for cinema in CINEMAS:
        cid = cinema["cinema_id"]
        formats = CINEMA_FORMAT_ROTATION.get(cid, DEFAULT_FORMAT_ROTATION)
        slots = []
        for movie in MOVIE_CATALOG:
            for i, time in enumerate(TIME_SLOTS):
                slots.append({"movie": movie, "time": time, "format": formats[i % len(formats)]})
        template[cid] = slots
    return template


SHOW_TEMPLATE: dict[str, list[dict]] = _build_show_template()

# ── City picker data ────────────────────────────────────────────────────────────
# Cities with actual ReelKart Theatres branches (drives which cinemas show up).
CITIES_WITH_CINEMAS = sorted({c["city"] for c in CINEMAS})


# ── Connection ─────────────────────────────────────────────────────────────────
def get_conn():
    """Return a fresh MySQL connection, creating the database if needed."""
    db_name = DB_CONFIG["database"]
    try:
        return mysql.connector.connect(**DB_CONFIG)

    except mysql.connector.errors.ProgrammingError as e:
        if e.errno == 1049:                          # unknown database
            server_cfg = {k: v for k, v in DB_CONFIG.items() if k != "database"}
            conn = mysql.connector.connect(**server_cfg)
            cur  = conn.cursor()
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{db_name}`")
            cur.execute(f"USE `{db_name}`")
            cur.close()
            return conn
        raise

    except Exception:
        raise


def ensure_tables():
    """Create tables on first run, and seed cinemas/showtimes if empty.

    NOTE — schema change from v2: booking tables are now keyed by show_id
    (cinema + movie + date + time + format) instead of a bare movie/show_time
    pair. If you're upgrading from a v2 database that already has `theatre` /
    `seats_booked` tables in the old shape, drop those two tables once before
    restarting the app so they get recreated in the new shape:
        DROP TABLE IF EXISTS seats_booked;
        DROP TABLE IF EXISTS theatre;
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"USE `{DB_CONFIG['database']}`")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS cinemas (
                cinema_id  VARCHAR(50)  PRIMARY KEY,
                name       VARCHAR(150),
                city       VARCHAR(100),
                location   VARCHAR(200),
                amenities  VARCHAR(255)
            )
        """)
        # Migration: add the column if this table already existed from before
        # multi-city support (harmless no-op on a fresh table).
        cur.execute("""
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'cinemas' AND COLUMN_NAME = 'city'
        """, (DB_CONFIG["database"],))
        if cur.fetchone()[0] == 0:
            cur.execute("ALTER TABLE cinemas ADD COLUMN city VARCHAR(100)")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS shows (
                id          INTEGER      AUTO_INCREMENT PRIMARY KEY,
                cinema_id   VARCHAR(50)  NOT NULL,
                movie_name  VARCHAR(100) NOT NULL,
                show_date   DATE         NOT NULL,
                show_time   VARCHAR(20)  NOT NULL,
                format_tag  VARCHAR(50),
                UNIQUE KEY uq_show (cinema_id, movie_name, show_date, show_time),
                CONSTRAINT fk_shows_cinema FOREIGN KEY (cinema_id)
                    REFERENCES cinemas(cinema_id)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER      AUTO_INCREMENT PRIMARY KEY,
                email         VARCHAR(150) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                first_name    VARCHAR(50),
                last_name     VARCHAR(50),
                phone         VARCHAR(30),
                sex           VARCHAR(10),
                role          VARCHAR(20)  DEFAULT 'customer',
                created_at    DATETIME
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS theatre (
                Customer_ID  VARCHAR(50)  PRIMARY KEY,
                show_id      INTEGER      NOT NULL,
                User_ID      INTEGER,
                Phone        VARCHAR(30),
                No_Tickets   INTEGER,
                Sex          VARCHAR(10),
                First_Name   VARCHAR(50),
                Last_Name    VARCHAR(50),
                Email_ID     VARCHAR(100),
                Mode_payment VARCHAR(50),
                Addons_JSON  TEXT,
                Promo_Code   VARCHAR(30),
                Discount_Amount INTEGER DEFAULT 0,
                CONSTRAINT fk_theatre_show FOREIGN KEY (show_id)
                    REFERENCES shows(id),
                CONSTRAINT fk_theatre_user FOREIGN KEY (User_ID)
                    REFERENCES users(id)
            )
        """)
        # Migrations: add columns if this table already existed from before
        # a given feature (harmless no-ops on a fresh table).
        for column, ddl in [
            ("Addons_JSON", "ALTER TABLE theatre ADD COLUMN Addons_JSON TEXT"),
            ("User_ID", "ALTER TABLE theatre ADD COLUMN User_ID INTEGER"),
            ("Promo_Code", "ALTER TABLE theatre ADD COLUMN Promo_Code VARCHAR(30)"),
            ("Discount_Amount", "ALTER TABLE theatre ADD COLUMN Discount_Amount INTEGER DEFAULT 0"),
        ]:
            cur.execute("""
                SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'theatre' AND COLUMN_NAME = %s
            """, (DB_CONFIG["database"], column))
            if cur.fetchone()[0] == 0:
                cur.execute(ddl)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS seats_booked (
                id           INTEGER     AUTO_INCREMENT PRIMARY KEY,
                Customer_ID  VARCHAR(50) NOT NULL,
                show_id      INTEGER     NOT NULL,
                seat_label   VARCHAR(10),
                ticket_type  VARCHAR(20),
                amount       INTEGER,
                booked_on    DATETIME,
                UNIQUE KEY uq_seat (show_id, seat_label),
                CONSTRAINT fk_seats_show FOREIGN KEY (show_id)
                    REFERENCES shows(id)
            )
        """)

        # Temporary seat reservations — held for a few minutes while someone
        # is picking seats/filling out payment, so two people can't both
        # think they've got the same seat mid-checkout. Expired holds are
        # cleaned up lazily (see cleanup_expired_holds) rather than needing
        # a background scheduler.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS seat_holds (
                show_id     INTEGER     NOT NULL,
                seat_label  VARCHAR(10) NOT NULL,
                held_until  DATETIME    NOT NULL,
                PRIMARY KEY (show_id, seat_label),
                CONSTRAINT fk_holds_show FOREIGN KEY (show_id)
                    REFERENCES shows(id)
            )
        """)
        conn.commit()

        _seed_cinemas_and_shows(cur)
        conn.commit()

        logger.info("Tables verified / created / seeded.")
    finally:
        conn.close()


def _chunked(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _seed_cinemas_and_shows(cur) -> None:
    """Insert/update cinema branches + a rolling window of showtimes.

    Idempotent by design (INSERT ... ON DUPLICATE KEY UPDATE / INSERT IGNORE)
    rather than gated on 'table is empty' — so adding a new cinema branch or
    city to the CINEMAS/SHOW_TEMPLATE constants takes effect on the next
    restart even against a database that was already seeded before.

    Showtimes are inserted in batched multi-row statements rather than one
    execute() per row — at ~29,000 rows (58 cinemas x 12 movies x 6 slots x 7
    days), a naive per-row loop would mean tens of thousands of individual
    DB round-trips on every single startup, which is slow enough to notice.
    """
    for c in CINEMAS:
        cur.execute(
            """INSERT INTO cinemas (cinema_id, name, city, location, amenities)
               VALUES (%s,%s,%s,%s,%s)
               ON DUPLICATE KEY UPDATE
                   name=VALUES(name), city=VALUES(city),
                   location=VALUES(location), amenities=VALUES(amenities)""",
            (c["cinema_id"], c["name"], c["city"], c["location"], ", ".join(c["amenities"])),
        )
    logger.info("Seeded/updated %d cinema branches across %d cities.",
                len(CINEMAS), len(CITIES_WITH_CINEMAS))

    today = datetime.date.today()
    all_rows = [
        (cinema_id, slot["movie"], today + datetime.timedelta(days=day_offset), slot["time"], slot["format"])
        for day_offset in range(SHOW_WINDOW_DAYS)
        for cinema_id, slots in SHOW_TEMPLATE.items()
        for slot in slots
    ]

    BATCH_SIZE = 500
    inserted = 0
    for batch in _chunked(all_rows, BATCH_SIZE):
        placeholders = ",".join(["(%s,%s,%s,%s,%s)"] * len(batch))
        flat_params = [v for row in batch for v in row]
        cur.execute(
            f"""INSERT IGNORE INTO shows
                (cinema_id, movie_name, show_date, show_time, format_tag)
                VALUES {placeholders}""",
            flat_params,
        )
        inserted += cur.rowcount
    logger.info("Seeded %d new showtime rows (of %d total) across %d days.",
                inserted, len(all_rows), SHOW_WINDOW_DAYS)


# ── Seat section map ───────────────────────────────────────────────────────────
def build_seat_section_map():
    """Populate SEAT_SECTION_MAP from SEAT_LAYOUT — called once at startup."""
    for section_name, row_labels, n_cols, _ in SEAT_LAYOUT:
        for r in row_labels:
            for c in range(1, n_cols + 1):
                SEAT_SECTION_MAP[f"{r}{c}"] = section_name


# ── Pricing ────────────────────────────────────────────────────────────────────
def price_for_seat(ticket_type: str, seat: str) -> int:
    section = SEAT_SECTION_MAP.get(seat, "Middle")
    base    = BASE_PRICES.get(ticket_type, 0)
    mod     = SEAT_TIER_MOD.get(section, 0)
    return max(base + mod, MIN_SEAT_PRICE)


def resolve_addons(addons: list[dict] | None) -> list[dict]:
    """Validate requested addon items against the catalog and return them
    enriched with name/price/line_total. Unknown item_ids or non-positive
    quantities are silently dropped rather than erroring the whole booking.
    """
    if not addons:
        return []
    resolved = []
    for a in addons:
        item = ADDON_BY_ID.get(a.get("item_id"))
        qty = a.get("qty", 0)
        if not item or not isinstance(qty, int) or qty <= 0:
            continue
        resolved.append({
            "item_id": item["item_id"],
            "name": item["name"],
            "icon": item["icon"],
            "price": item["price"],
            "qty": qty,
            "line_total": item["price"] * qty,
        })
    return resolved


def validate_promo_code(code: str | None, pretax_amount: int) -> dict:
    """Validate a promo code against a pre-tax amount and return the
    discount to apply. Never raises — an invalid/missing code just means
    no discount, so callers can use this unconditionally.
    """
    if not code:
        return {"valid": False, "code": None, "discount_amount": 0, "message": ""}

    promo = PROMO_CODES.get(code.upper().strip())
    if not promo:
        return {"valid": False, "code": code, "discount_amount": 0, "message": "Invalid promo code."}

    if promo["type"] == "percent":
        discount = round(pretax_amount * promo["value"] / 100)
        if promo.get("max_discount"):
            discount = min(discount, promo["max_discount"])
    else:
        discount = promo["value"]

    discount = max(0, min(discount, pretax_amount))   # never negative, never more than the subtotal
    return {
        "valid": True, "code": code.upper().strip(),
        "discount_amount": discount, "message": promo["description"],
    }


def compute_totals(
    ticket_type: str, seats: list[str], addons: list[dict] | None = None,
    promo_code: str | None = None,
) -> tuple[int, int, int, int, int]:
    """Return (seat_subtotal, addon_subtotal, discount_amount, tax_amount, total_amount).

    A promo code discount (if valid) is applied to seats + add-ons *before*
    tax; tax is then computed on the discounted amount.
    """
    seat_subtotal = sum(price_for_seat(ticket_type, s) for s in seats)
    addon_subtotal = sum(a["line_total"] for a in resolve_addons(addons))
    pretax = seat_subtotal + addon_subtotal

    promo_result = validate_promo_code(promo_code, pretax)
    discount_amount = promo_result["discount_amount"]

    discounted_pretax = pretax - discount_amount
    tax_amount = round(discounted_pretax * TAX_RATE)
    total_amount = discounted_pretax + tax_amount
    return seat_subtotal, addon_subtotal, discount_amount, tax_amount, total_amount


def split_stored_total(total_amount: int) -> tuple[int, int]:
    """Best-effort reconstruction of (pre-tax subtotal, tax_amount) from a
    stored tax-inclusive total — used when redisplaying a past booking,
    since only the final total is persisted in the database. Note this
    collapses seats + add-ons into a single 'subtotal' figure since the
    breakdown itself isn't separately stored.
    """
    subtotal = round(total_amount / (1 + TAX_RATE))
    tax_amount = total_amount - subtotal
    return subtotal, tax_amount


# ── Validation ─────────────────────────────────────────────────────────────────
def validate_email(email: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email))


def validate_phone(phone: str) -> bool:
    return bool(re.match(r"^[\d\s\+\-\(\)]{7,15}$", phone))


# ── Movies / cinemas / showtimes ───────────────────────────────────────────────
def list_movies() -> list[str]:
    return list(MOVIE_CATALOG)


def list_addons() -> list[dict]:
    return list(ADDON_CATALOG)


def list_cities() -> dict:
    """City picker data: popular grid + broader alphabetical list, plus which
    cities actually have a ReelKart Theatres branch (for empty-state handling).
    """
    return {
        "popular": list(POPULAR_CITIES),
        "other": list(OTHER_CITIES),
        "cities_with_cinemas": list(CITIES_WITH_CINEMAS),
    }


def _sort_key_for_time(show_time: str) -> int:
    """Minutes since midnight, for sorting a day's showtimes chronologically.

    Late-night shows (before 6 AM) are treated as belonging to the end of
    the previous day's lineup rather than the start of the next one — e.g.
    a 1:00 AM show sorts after 11:00 PM, not before 9:00 AM.
    """
    try:
        t = datetime.datetime.strptime(show_time, "%I:%M %p").time()
    except ValueError:
        return 0
    minutes = t.hour * 60 + t.minute
    if t.hour < 6:
        minutes += 24 * 60
    return minutes


def get_showtimes(movie: str, show_date: str, city: str | None = None) -> list[dict]:
    """Return, for a given movie + date (optionally scoped to one city),
    every cinema showing it that day with its showtimes and live fill
    status — used to power the 'showtimes by cinema' listing page.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"USE `{DB_CONFIG['database']}`")
        if city:
            cur.execute(
                """SELECT s.id, s.cinema_id, c.name, c.location, c.amenities,
                          s.show_time, s.format_tag
                   FROM shows s
                   JOIN cinemas c ON c.cinema_id = s.cinema_id
                   WHERE s.movie_name = %s AND s.show_date = %s AND c.city = %s
                   ORDER BY c.name""",
                (movie, show_date, city),
            )
        else:
            cur.execute(
                """SELECT s.id, s.cinema_id, c.name, c.location, c.amenities,
                          s.show_time, s.format_tag
                   FROM shows s
                   JOIN cinemas c ON c.cinema_id = s.cinema_id
                   WHERE s.movie_name = %s AND s.show_date = %s
                   ORDER BY c.name""",
                (movie, show_date),
            )
        rows = cur.fetchall()

        total_seats = sum(len(rl) * cols for _, rl, cols, _ in SEAT_LAYOUT)

        by_cinema: dict[str, dict] = {}
        for show_id, cinema_id, name, location, amenities, show_time, fmt in rows:
            cur.execute(
                "SELECT COUNT(*) FROM seats_booked WHERE show_id=%s", (show_id,)
            )
            booked_count = cur.fetchone()[0]
            ratio = booked_count / total_seats if total_seats else 0
            if ratio >= 1:
                status = "full"
            elif ratio >= 0.8:
                status = "almost"
            else:
                status = "available"

            entry = by_cinema.setdefault(cinema_id, {
                "cinema_id": cinema_id,
                "name": name,
                "location": location,
                "amenities": amenities.split(", ") if amenities else [],
                "shows": [],
            })
            entry["shows"].append({
                "show_id": show_id,
                "show_time": show_time,
                "format": fmt,
                "status": status,
            })

        result = list(by_cinema.values())
        for entry in result:
            entry["shows"].sort(key=lambda s: _sort_key_for_time(s["show_time"]))
        return result
    finally:
        conn.close()


def get_show(show_id: int) -> dict | None:
    """Return cinema + movie + date/time/format details for a single show_id."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"USE `{DB_CONFIG['database']}`")
        cur.execute(
            """SELECT s.id, s.movie_name, s.show_date, s.show_time, s.format_tag,
                      c.cinema_id, c.name, c.location
               FROM shows s
               JOIN cinemas c ON c.cinema_id = s.cinema_id
               WHERE s.id = %s""",
            (show_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "show_id":    row[0],
            "movie":      row[1],
            "show_date":  str(row[2]),
            "show_time":  row[3],
            "format":     row[4],
            "cinema_id":  row[5],
            "cinema_name": row[6],
            "cinema_location": row[7],
        }
    finally:
        conn.close()


# ── Seats ──────────────────────────────────────────────────────────────────────
SEAT_HOLD_MINUTES = 10   # how long a seat stays reserved once someone selects it

def load_booked_seats(show_id: int) -> set[str]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"USE `{DB_CONFIG['database']}`")
        cur.execute(
            "SELECT seat_label FROM seats_booked WHERE show_id=%s",
            (show_id,),
        )
        return {r[0] for r in cur.fetchall()}
    finally:
        conn.close()


def _cleanup_expired_holds(cur) -> None:
    cur.execute("DELETE FROM seat_holds WHERE held_until < NOW()")


def get_held_seats(show_id: int) -> set[str]:
    """Seats currently reserved (by anyone) for this show, excluding expired
    holds — used so other viewers see them as unavailable in real time.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"USE `{DB_CONFIG['database']}`")
        _cleanup_expired_holds(cur)
        conn.commit()
        cur.execute(
            "SELECT seat_label FROM seat_holds WHERE show_id=%s AND held_until > NOW()",
            (show_id,),
        )
        return {r[0] for r in cur.fetchall()}
    finally:
        conn.close()


def hold_seats(show_id: int, seats: list[str]) -> tuple[list[str], list[str]]:
    """Try to place a temporary hold on each seat. Returns (held, rejected) —
    `held` is the subset that were successfully reserved for this caller;
    `rejected` is the subset already held by someone else (or booked, though
    the caller should also re-check against load_booked_seats separately).
    A hold lasts SEAT_HOLD_MINUTES from now.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"USE `{DB_CONFIG['database']}`")
        _cleanup_expired_holds(cur)

        held_until = datetime.datetime.now() + datetime.timedelta(minutes=SEAT_HOLD_MINUTES)
        held, rejected = [], []
        for s in seats:
            try:
                cur.execute(
                    "INSERT INTO seat_holds (show_id, seat_label, held_until) VALUES (%s,%s,%s)",
                    (show_id, s, held_until),
                )
                held.append(s)
            except mysql.connector.errors.IntegrityError:
                rejected.append(s)   # already held by someone else and not yet expired
        conn.commit()
        return held, rejected
    finally:
        conn.close()


def release_seats(show_id: int, seats: list[str]) -> None:
    """Release holds on the given seats (e.g. the user deselected them, left
    the page, or completed a real booking that supersedes the hold).
    """
    if not seats:
        return
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"USE `{DB_CONFIG['database']}`")
        placeholders = ",".join(["%s"] * len(seats))
        cur.execute(
            f"DELETE FROM seat_holds WHERE show_id=%s AND seat_label IN ({placeholders})",
            (show_id, *seats),
        )
        conn.commit()
    finally:
        conn.close()


# ── Booking ────────────────────────────────────────────────────────────────────
def commit_booking(
    customer_id: str,
    show_id: int,
    cust_info: dict,
    ticket_type: str,
    seats: list[str],
    total_amount: int,
    addons: list[dict] | None = None,
    user_id: int | None = None,
    promo_code: str | None = None,
    discount_amount: int = 0,
) -> None:
    """Insert into both theatre + seats_booked tables in one transaction.
    `addons` should be pre-resolved (via resolve_addons) with name/price/line_total.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"USE `{DB_CONFIG['database']}`")

        cur.execute(
            """INSERT INTO theatre
               (Customer_ID, show_id, User_ID, Phone, No_Tickets, Sex,
                First_Name, Last_Name, Email_ID, Mode_payment, Addons_JSON,
                Promo_Code, Discount_Amount)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                customer_id, show_id, user_id,
                cust_info.get("phone", ""),
                len(seats),
                cust_info.get("sex", ""),
                cust_info.get("first_name", ""),
                cust_info.get("last_name", ""),
                cust_info.get("email", ""),
                cust_info.get("payment_mode", ""),
                json.dumps(addons or []),
                promo_code,
                discount_amount,
            ),
        )

        now = datetime.datetime.now()
        for s in seats:
            cur.execute(
                """INSERT INTO seats_booked
                   (Customer_ID, show_id, seat_label,
                    ticket_type, amount, booked_on)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (customer_id, show_id, s, ticket_type, total_amount, now),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Cancel ─────────────────────────────────────────────────────────────────────
def cancel_booking(customer_id: str) -> None:
    """Delete from both tables in one transaction."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"USE `{DB_CONFIG['database']}`")
        cur.execute("DELETE FROM seats_booked WHERE Customer_ID=%s", (customer_id,))
        cur.execute("DELETE FROM theatre       WHERE Customer_ID=%s", (customer_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Lookup ─────────────────────────────────────────────────────────────────────
def get_booking(customer_id: str) -> dict | None:
    """Return a structured booking dict, or None if not found."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"USE `{DB_CONFIG['database']}`")
        cur.execute(
            "SELECT show_id, seat_label, ticket_type, amount, booked_on "
            "FROM seats_booked WHERE Customer_ID=%s",
            (customer_id,),
        )
        rows = cur.fetchall()
        if not rows:
            return None

        show_id = rows[0][0]
        show = get_show(show_id)

        # Also pull customer info (including F&B add-ons ordered with this booking)
        cur.execute(
            "SELECT First_Name, Last_Name, Phone, Email_ID, Sex, Mode_payment, Addons_JSON, "
            "User_ID, Promo_Code, Discount_Amount "
            "FROM theatre WHERE Customer_ID=%s",
            (customer_id,),
        )
        cust = cur.fetchone()
        addons = []
        if cust and cust[6]:
            try:
                addons = json.loads(cust[6])
            except (ValueError, TypeError):
                addons = []

        return {
            "customer_id":  customer_id,
            "show_id":      show_id,
            "user_id":      cust[7] if cust else None,
            "promo_code":   cust[8] if cust else None,
            "discount_amount": cust[9] if cust and cust[9] else 0,
            "movie":        show["movie"] if show else "",
            "cinema_name":  show["cinema_name"] if show else "",
            "show_date":    show["show_date"] if show else "",
            "show_time":    show["show_time"] if show else rows[0][1],
            "format":       show["format"] if show else "",
            "ticket_type":  rows[0][2],
            "total_amount": rows[0][3],
            "booked_on":    str(rows[0][4]),
            "seats":        sorted(r[1] for r in rows),
            "first_name":   cust[0] if cust else "",
            "last_name":    cust[1] if cust else "",
            "phone":        cust[2] if cust else "",
            "email":        cust[3] if cust else "",
            "sex":          cust[4] if cust else "",
            "payment_mode": cust[5] if cust else "",
            "addons":       addons,
        }
    finally:
        conn.close()


# ── Admin search ───────────────────────────────────────────────────────────────
def admin_search_bookings(movie_q: str = "", cid_q: str = "") -> list[dict]:
    """Return aggregated bookings, optionally filtered by movie name or customer ID."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"USE `{DB_CONFIG['database']}`")

        base = (
            "SELECT sb.Customer_ID, s.movie_name, c.name, s.show_date, s.show_time, "
            "sb.seat_label, sb.ticket_type, sb.amount, sb.booked_on "
            "FROM seats_booked sb "
            "JOIN shows s ON s.id = sb.show_id "
            "JOIN cinemas c ON c.cinema_id = s.cinema_id"
        )
        if cid_q:
            cur.execute(base + " WHERE sb.Customer_ID=%s ORDER BY sb.booked_on DESC",
                        (cid_q,))
        elif movie_q:
            cur.execute(base + " WHERE s.movie_name LIKE %s ORDER BY sb.booked_on DESC",
                        (f"%{movie_q}%",))
        else:
            cur.execute(base + " ORDER BY sb.booked_on DESC LIMIT 500")

        rows = cur.fetchall()
    finally:
        conn.close()

    # Aggregate multiple seat rows → one booking dict
    aggregated: dict[tuple, list] = {}
    for cid, movie, cinema_name, show_date, show_time, seat, ttype, amt, bon in rows:
        key = (cid, movie, cinema_name, str(show_date), show_time, ttype, amt, str(bon))
        aggregated.setdefault(key, []).append(seat)

    results = []
    for (cid, movie, cinema_name, show_date, show_time, ttype, amt, bon), seats in aggregated.items():
        results.append({
            "customer_id":  cid,
            "movie":        movie,
            "cinema_name":  cinema_name,
            "show_date":    show_date,
            "show_time":    show_time,
            "ticket_type":  ttype,
            "total_amount": amt,
            "booked_on":    bon,
            "seats":        sorted(seats),
        })
    return results


# ── Users / accounts ────────────────────────────────────────────────────────────
def create_user(
    email: str, password_hash: str, first_name: str, last_name: str,
    phone: str, sex: str, role: str = "customer",
) -> dict:
    """Create a new user account and return it. Raises mysql IntegrityError
    (caller should catch) if the email is already registered.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"USE `{DB_CONFIG['database']}`")
        cur.execute(
            """INSERT INTO users
               (email, password_hash, first_name, last_name, phone, sex, role, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (email, password_hash, first_name, last_name, phone, sex, role, datetime.datetime.now()),
        )
        conn.commit()
        user_id = cur.lastrowid
        return get_user_by_id(user_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_user_by_email(email: str) -> dict | None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"USE `{DB_CONFIG['database']}`")
        cur.execute(
            "SELECT id, email, password_hash, first_name, last_name, phone, sex, role "
            "FROM users WHERE email=%s",
            (email,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0], "email": row[1], "password_hash": row[2],
            "first_name": row[3], "last_name": row[4], "phone": row[5],
            "sex": row[6], "role": row[7],
        }
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> dict | None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"USE `{DB_CONFIG['database']}`")
        cur.execute(
            "SELECT id, email, password_hash, first_name, last_name, phone, sex, role "
            "FROM users WHERE id=%s",
            (user_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0], "email": row[1], "password_hash": row[2],
            "first_name": row[3], "last_name": row[4], "phone": row[5],
            "sex": row[6], "role": row[7],
        }
    finally:
        conn.close()


def seed_admin_user(email: str, password_hash: str) -> None:
    """Ensure the admin account exists with the given (bcrypt-hashed)
    password, called once at startup from main.py. Safe to call every
    startup — inserts only if missing.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"USE `{DB_CONFIG['database']}`")
        cur.execute("SELECT id FROM users WHERE email=%s", (email,))
        if cur.fetchone():
            return
        cur.execute(
            """INSERT INTO users
               (email, password_hash, first_name, last_name, phone, sex, role, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (email, password_hash, "Admin", "", "", "", "admin", datetime.datetime.now()),
        )
        conn.commit()
        logger.info("Seeded admin account (%s).", email)
    finally:
        conn.close()


def list_bookings_for_user(user_id: int) -> list[dict]:
    """Return every booking made by this user account, most recent first —
    powers the 'My Bookings' page once someone is logged in.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"USE `{DB_CONFIG['database']}`")
        cur.execute(
            "SELECT sb.Customer_ID, s.movie_name, c.name, s.show_date, s.show_time, "
            "sb.seat_label, sb.ticket_type, sb.amount, sb.booked_on "
            "FROM seats_booked sb "
            "JOIN theatre th ON th.Customer_ID = sb.Customer_ID "
            "JOIN shows s ON s.id = sb.show_id "
            "JOIN cinemas c ON c.cinema_id = s.cinema_id "
            "WHERE th.User_ID = %s "
            "ORDER BY sb.booked_on DESC",
            (user_id,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    aggregated: dict[tuple, list] = {}
    for cid, movie, cinema_name, show_date, show_time, seat, ttype, amt, bon in rows:
        key = (cid, movie, cinema_name, str(show_date), show_time, ttype, amt, str(bon))
        aggregated.setdefault(key, []).append(seat)

    results = []
    for (cid, movie, cinema_name, show_date, show_time, ttype, amt, bon), seats in aggregated.items():
        results.append({
            "customer_id":  cid,
            "movie":        movie,
            "cinema_name":  cinema_name,
            "show_date":    show_date,
            "show_time":    show_time,
            "ticket_type":  ttype,
            "total_amount": amt,
            "booked_on":    bon,
            "seats":        sorted(seats),
        })
    return results


def get_analytics(days: int = 14) -> dict:
    """Aggregated stats for the admin analytics dashboard.

    IMPORTANT: `seats_booked.amount` stores the *whole booking's* total,
    repeated on every seat row (so a 3-seat booking has the same amount on
    3 rows). Summing that column directly would overcount revenue 3x for
    that booking — so revenue figures are always computed from a
    per-booking view (one row per Customer_ID) rather than raw seat rows.
    Ticket counts and occupancy, by contrast, correctly use raw seat rows,
    since each row genuinely does represent one ticket.
    """
    from collections import defaultdict

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"USE `{DB_CONFIG['database']}`")

        # One row per booking — the basis for every revenue figure below.
        cur.execute("""
            SELECT Customer_ID, MAX(amount) AS amount, MAX(booked_on) AS booked_on,
                   MAX(show_id) AS show_id, MAX(ticket_type) AS ticket_type
            FROM seats_booked
            GROUP BY Customer_ID
        """)
        bookings = cur.fetchall()

        total_revenue = sum(b[1] for b in bookings)
        total_bookings = len(bookings)

        # Ticket counts + occupancy correctly use raw seat rows (one row = one ticket).
        cur.execute("SELECT show_id, COUNT(*) FROM seats_booked GROUP BY show_id")
        seats_per_show = dict(cur.fetchall())
        tickets_sold = sum(seats_per_show.values())

        total_seats = sum(len(rows) * cols for _, rows, cols, _ in SEAT_LAYOUT)
        occupancy_values = [count / total_seats for count in seats_per_show.values()] if total_seats else []
        avg_occupancy_pct = round(sum(occupancy_values) / len(occupancy_values) * 100, 1) if occupancy_values else 0.0

        # Revenue + booking count per day, for the trend chart.
        revenue_by_day = defaultdict(int)
        bookings_by_day = defaultdict(int)
        for _cid, amount, booked_on, _show_id, _ttype in bookings:
            day = booked_on.date().isoformat() if hasattr(booked_on, "date") else str(booked_on)[:10]
            revenue_by_day[day] += amount
            bookings_by_day[day] += 1

        today = datetime.date.today()
        daily_series = []
        for i in range(days - 1, -1, -1):
            d = (today - datetime.timedelta(days=i)).isoformat()
            daily_series.append({
                "date": d, "revenue": revenue_by_day.get(d, 0), "bookings": bookings_by_day.get(d, 0),
            })

        # Ticket-type split (by tickets sold, from raw seat rows).
        cur.execute("SELECT ticket_type, COUNT(*) FROM seats_booked GROUP BY ticket_type")
        ticket_type_breakdown = [{"ticket_type": t, "count": c} for t, c in cur.fetchall()]

        # Most popular movies by tickets sold.
        cur.execute("""
            SELECT s.movie_name, COUNT(*) AS tickets
            FROM seats_booked sb JOIN shows s ON s.id = sb.show_id
            GROUP BY s.movie_name ORDER BY tickets DESC LIMIT 8
        """)
        popular_movies = [{"movie": m, "tickets": c} for m, c in cur.fetchall()]

        # Revenue by cinema branch (per-booking, to avoid the overcount issue).
        cur.execute("SELECT s.id, c.name FROM shows s JOIN cinemas c ON c.cinema_id = s.cinema_id")
        show_to_cinema = dict(cur.fetchall())
        revenue_by_cinema = defaultdict(int)
        for _cid, amount, _booked_on, show_id, _ttype in bookings:
            revenue_by_cinema[show_to_cinema.get(show_id, "Unknown")] += amount
        cinema_breakdown = [
            {"cinema_name": k, "revenue": v}
            for k, v in sorted(revenue_by_cinema.items(), key=lambda x: -x[1])
        ]

        return {
            "total_revenue": total_revenue,
            "total_bookings": total_bookings,
            "tickets_sold": tickets_sold,
            "avg_occupancy_pct": avg_occupancy_pct,
            "daily_series": daily_series,
            "ticket_type_breakdown": ticket_type_breakdown,
            "popular_movies": popular_movies,
            "cinema_breakdown": cinema_breakdown,
        }
    finally:
        conn.close()


# ── ID generator ───────────────────────────────────────────────────────────────
def generate_customer_id() -> str:
    return str(uuid.uuid4())[:8].upper()


# ── Receipt path ───────────────────────────────────────────────────────────────
def receipt_path_for(customer_id: str) -> Path:
    return RECEIPTS_DIR / f"Ticket_{customer_id}.html"
