# ==============================================================================
# receipt.py  —  ReelKart Theatres  |  HTML receipt generator
# Dark BMS-style receipt — same design as v2 desktop app.
# ==============================================================================

import datetime
import html as _html
import base64
import io
from pathlib import Path

import qrcode


def _generate_qr_data_uri(payload: str) -> str:
    """Generate a QR code PNG for the given payload and return it as a
    base64 data URI, so the receipt HTML file stays fully self-contained
    (no separate image file to manage/serve).
    """
    img = qrcode.make(payload, box_size=6, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def build_receipt_html(customer_id: str, info: dict) -> str:
    """
    Build the dark-themed HTML receipt as a string.

    info keys:
        movie, show_time, ticket_type, seats (list), total_amount,
        first_name, last_name, phone, email, payment_mode
    """
    name  = f"{info['first_name']} {info['last_name']}".strip() or "—"
    seats = info.get("seats", [])
    n     = len(seats)
    word  = "ticket" if n == 1 else "tickets"
    subtotal       = info.get("subtotal")
    addon_subtotal = info.get("addon_subtotal") or 0
    addons         = info.get("addons") or []
    tax_amount     = info.get("tax_amount")
    promo_code       = info.get("promo_code")
    discount_amount  = info.get("discount_amount") or 0

    seat_chips = "".join(
        f'<span class="chip">{_html.escape(s)}</span>'
        for s in seats
    )

    addon_rows = "".join(
        f'<div class="brow"><span>{_html.escape(a["icon"])} {_html.escape(a["name"])} &times;{a["qty"]}</span>'
        f'<span>₹{a["line_total"]:,}</span></div>'
        for a in addons
    )

    qr_payload = (
        f"ReelKart Theatres Ticket\n"
        f"Booking ID: {customer_id}\n"
        f"Movie: {info.get('movie', '')}\n"
        f"Theatre: {info.get('cinema_name', '')}\n"
        f"Date: {info.get('show_date', '')}\n"
        f"Time: {info.get('show_time', '')}\n"
        f"Seats: {', '.join(seats)}"
    )
    qr_data_uri = _generate_qr_data_uri(qr_payload)

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<title>Booking Confirmation — ReelKart Theatres</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',system-ui,sans-serif;background:#08080F;
     color:#F1F5F9;min-height:100vh;display:flex;
     align-items:center;justify-content:center;padding:32px 16px}}
.card{{background:#161628;border:1px solid #2A2A50;border-radius:16px;
       max-width:620px;width:100%;overflow:hidden;
       box-shadow:0 24px 64px rgba(0,0,0,.7)}}
.hdr{{background:linear-gradient(135deg,#E03A50 0%,#900020 100%);
      padding:28px 32px;text-align:center}}
.hdr h1{{font-size:22px;font-weight:800;letter-spacing:.5px}}
.hdr .sub{{font-size:12px;opacity:.8;margin-top:4px;letter-spacing:1px;
           text-transform:uppercase}}
.bid{{background:#0F0F1E;padding:13px 32px;text-align:center;
      font-size:12px;color:#64748B;letter-spacing:.5px;
      border-bottom:1px solid #2A2A50}}
.bid span{{color:#FBBF24;font-weight:700;font-size:15px;
           font-family:'Courier New',monospace;letter-spacing:2px}}
.body{{padding:28px 32px}}
.movie{{font-size:20px;font-weight:800;color:#F1F5F9;margin-bottom:3px}}
.show{{font-size:12px;color:#94A3B8;margin-bottom:16px}}
.badge{{display:inline-block;background:#1E1E3A;border:1px solid #2A2A50;
        border-radius:20px;padding:4px 16px;font-size:11px;
        color:#A78BFA;font-weight:700;margin-bottom:22px}}
hr{{border:none;border-top:1px solid #1E1E3A;margin:20px 0}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}
.f label{{font-size:10px;text-transform:uppercase;letter-spacing:.8px;
          color:#4B5580;display:block;margin-bottom:5px}}
.f .v{{font-size:14px;color:#F1F5F9;font-weight:600}}
.chips-hdr{{font-size:10px;text-transform:uppercase;letter-spacing:.8px;
            color:#4B5580;margin-bottom:10px;margin-top:20px}}
.chips{{display:flex;flex-wrap:wrap;gap:8px}}
.chip{{background:#E03A50;color:#fff;padding:6px 14px;border-radius:6px;
       font-size:13px;font-weight:800;font-family:'Courier New',monospace}}
.total{{background:#0F0F1E;border:1px solid #2A2A50;border-radius:12px;
        padding:20px 24px;margin-top:22px;
        display:flex;align-items:center;justify-content:space-between}}
.tlbl{{font-size:12px;color:#94A3B8;text-transform:uppercase;letter-spacing:.8px}}
.tamt{{font-size:30px;font-weight:800;color:#22C55E}}
.tamt span{{font-size:18px;margin-right:1px}}
.breakdown{{margin-top:18px}}
.brow{{display:flex;justify-content:space-between;font-size:13px;
       color:#94A3B8;padding:6px 2px}}
.brow.tax{{color:#F59E0B}}
.brow.discount{{color:#22C55E}}
.brow span:last-child{{color:#F1F5F9;font-weight:600}}
.footer{{padding:22px 32px;text-align:center;border-top:1px solid #1E1E3A}}
.btn{{display:inline-block;background:#E03A50;color:#fff;
      padding:13px 30px;border-radius:9px;text-decoration:none;
      font-weight:800;font-size:14px;cursor:pointer}}
.ts{{font-size:11px;color:#2E3A55;margin-top:12px}}
.qr-block{{display:flex;align-items:center;gap:18px;background:#0F0F1E;
           border:1px solid #2A2A50;border-radius:12px;padding:18px 24px;
           margin-top:14px}}
.qr-block img{{width:96px;height:96px;border-radius:8px;background:#fff;padding:6px}}
.qr-txt{{font-size:11px;color:#4B5580;text-transform:uppercase;letter-spacing:.6px}}
.qr-txt strong{{display:block;font-size:13px;color:#F1F5F9;text-transform:none;
                 letter-spacing:0;margin-top:4px;font-weight:700}}
</style></head><body>
<div class="card">
  <div class="hdr">
    <h1>🎬 ReelKart Theatres</h1>
    <div class="sub">Booking Confirmation</div>
  </div>
  <div class="bid">Booking ID: <span>{_html.escape(customer_id)}</span></div>
  <div class="body">
    <div class="movie">{_html.escape(info['movie'])}</div>
    <div class="show">{_html.escape(info.get('cinema_name',''))} &middot; {_html.escape(str(info.get('show_date','')))} &middot; {_html.escape(info['show_time'])}{f" &middot; {_html.escape(info.get('format',''))}" if info.get('format') else ""}</div>
    <span class="badge">{_html.escape(info['ticket_type'])}</span>
    <hr>
    <div class="grid">
      <div class="f"><label>Name</label>
        <div class="v">{_html.escape(name)}</div></div>
      <div class="f"><label>Phone</label>
        <div class="v">{_html.escape(info.get('phone','—'))}</div></div>
      <div class="f"><label>Email</label>
        <div class="v">{_html.escape(info.get('email','—'))}</div></div>
      <div class="f"><label>Payment</label>
        <div class="v">{_html.escape(info.get('payment_mode','—'))}</div></div>
    </div>
    <div class="chips-hdr">{n} {word} booked</div>
    <div class="chips">{seat_chips}</div>
    {"" if not addons else f'''
    <div class="chips-hdr" style="margin-top:20px;">Food &amp; Beverages</div>
    <div class="breakdown" style="margin-top:8px;">{addon_rows}</div>
    '''}
    {"" if subtotal is None else f'''
    <div class="breakdown">
      <div class="brow"><span>Seat subtotal</span><span>₹{subtotal:,}</span></div>
      {"" if not addon_subtotal else f'<div class="brow"><span>Food &amp; Beverage subtotal</span><span>₹{addon_subtotal:,}</span></div>'}
      {"" if not discount_amount else f'<div class="brow discount"><span>Promo {_html.escape(promo_code or "")}</span><span>&minus;₹{discount_amount:,}</span></div>'}
      <div class="brow tax"><span>Tax (20%)</span><span>₹{tax_amount:,}</span></div>
    </div>
    '''}
    <div class="total">
      <div class="tlbl">Total Paid</div>
      <div class="tamt"><span>₹</span>{info['total_amount']:,}</div>
    </div>
    <div class="qr-block">
      <img src="{qr_data_uri}" alt="Ticket QR code">
      <div class="qr-txt">Show this at the entrance
        <strong>Scan to verify booking {_html.escape(customer_id)}</strong>
      </div>
    </div>
  </div>
  <div class="footer">
    <a class="btn" href="#" onclick="window.print()">🖨  Print / Save as PDF</a>
    <div class="ts">Generated: {datetime.datetime.now().strftime('%d %b %Y, %I:%M %p')}</div>
  </div>
</div>
</body></html>"""


def generate_receipt(customer_id: str, info: dict, filepath: Path) -> None:
    """Write the HTML receipt to `filepath`."""
    filepath.write_text(build_receipt_html(customer_id, info), encoding="utf-8")


def render_receipt_pdf(customer_id: str, info: dict) -> bytes:
    """Render the same receipt as a PDF and return the raw bytes —
    used for the 'Download PDF' button and for email attachments.
    """
    from xhtml2pdf import pisa   # imported lazily so PDF support is optional
    import io

    html_content = build_receipt_html(customer_id, info)
    buf = io.BytesIO()
    pisa.CreatePDF(io.StringIO(html_content), dest=buf)
    return buf.getvalue()
