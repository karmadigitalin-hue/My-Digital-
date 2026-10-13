"""
Free WhatsApp Bot - Stock inquiry + Order forwarding
Stack: Flask + Meta WhatsApp Cloud API + Google Sheets (gspread)

TWO separate Google Sheet files:
  STOCK file  -> one tab per BRAND (tab name = brand name)
                 columns in every tab: Item No | Qty | Rate
  ORDERS file -> one tab named "Orders"
                 columns: Time | Client Number | Brand | Item No | Qty | Status
"""
import os, json, time, datetime, requests
from flask import Flask, request
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__)

# ---------- Settings (set these as environment variables) ----------
VERIFY_TOKEN   = os.environ["VERIFY_TOKEN"]        # any secret word you choose
WA_TOKEN       = os.environ["WA_TOKEN"]            # Meta permanent access token
PHONE_ID       = os.environ["PHONE_NUMBER_ID"]     # from Meta dashboard
OWNER_NUMBERS  = os.environ["OWNER_NUMBERS"].split(",")  # e.g. 919999999999,918888888888
IGNORE_NUMBERS = os.environ.get("IGNORE_NUMBERS", "").split(",")  # staff numbers bot should ignore
STOCK_SHEET_ID  = os.environ["STOCK_SHEET_ID"]
ORDERS_SHEET_ID = os.environ["ORDERS_SHEET_ID"]
GOOGLE_CREDS   = json.loads(os.environ["GOOGLE_CREDS_JSON"])  # service account json

# ---------- Google Sheets ----------
creds = Credentials.from_service_account_info(
    GOOGLE_CREDS, scopes=["https://www.googleapis.com/auth/spreadsheets"])
gc = gspread.authorize(creds)
stock_file  = gc.open_by_key(STOCK_SHEET_ID)
orders_file = gc.open_by_key(ORDERS_SHEET_ID)

_cache = {"t": 0, "data": {}}
def load_stock():
    """returns {brand: [(item_no, qty, rate), ...]} - cached 60 sec"""
    if time.time() - _cache["t"] > 60:
        data = {}
        for ws in stock_file.worksheets():
            rows = ws.get_all_values()[1:]          # skip heading row
            data[ws.title] = [(r[0].strip(), r[1], r[2]) for r in rows if r and r[0].strip()]
        _cache.update(t=time.time(), data=data)
    return _cache["data"]

def find_brand(q):
    q = q.lower().strip()
    return [b for b in load_stock() if q and q in b.lower()]

def find_item(item_no, brand=None):
    out = []
    for b, items in load_stock().items():
        if brand and brand.lower() not in b.lower():
            continue
        for no, qty, rate in items:
            if no.lower() == item_no.lower():
                out.append((b, no, qty, rate))
    return out

def add_order(client, brand, item_no, qty):
    orders_file.worksheet("Orders").append_row([
        datetime.datetime.now().strftime("%d-%m-%Y %H:%M"),
        client, brand, item_no, qty, "New"])

# ---------- WhatsApp send ----------
def send(to, text):
    requests.post(
        f"https://graph.facebook.com/v20.0/{PHONE_ID}/messages",
        headers={"Authorization": f"Bearer {WA_TOKEN}"},
        json={"messaging_product": "whatsapp", "to": to,
              "type": "text", "text": {"body": text}},
        timeout=15)

# ---------- Bot logic ----------
HELP = ("Namaste! 🙏\n"
        "• Brand ka stock dekhne ke liye brand ka naam likhein (jaise: *nike*)\n"
        "• Item ka stock dekhne ke liye item number likhein (jaise: *1001*)\n"
        "• Order ke liye likhein: *order 1001 10*\n"
        "  (agar item number kai brand me hai: *order nike 1001 10*)")

def handle(sender, text):
    t = text.strip()
    low = t.lower()

    if low in ("hi", "hello", "hii", "menu", "help"):
        return send(sender, HELP)

    # ORDER:  order <itemno> <qty>   OR   order <brand> <itemno> <qty>
    if low.startswith("order"):
        p = t.split()
        if len(p) < 3 or not p[-1].isdigit():
            return send(sender, "Order aise likhein: *order 1001 10*")
        qty = p[-1]
        if len(p) == 3:
            item_no, brand = p[1], None
        else:
            item_no, brand = p[-2], " ".join(p[1:-2])
        hits = find_item(item_no, brand)
        if not hits:
            return send(sender, "Item nahi mila. Brand aur item number check karein.")
        if len(hits) > 1:
            names = ", ".join(h[0] for h in hits)
            return send(sender, f"Ye item kai brand me hai ({names}). Aise likhein: *order <brand> {item_no} {qty}*")
        b, no, stock_qty, rate = hits[0]
        add_order(sender, b, no, qty)
        send(sender, f"✅ Order received!\n{b} - {no} x {qty}\nHum jaldi confirm karenge.")
        for owner in OWNER_NUMBERS:
            send(owner, f"🛒 NEW ORDER\nClient: +{sender}\nBrand: {b}\nItem No: {no}\nQty: {qty}")
        return

    # BRAND inquiry
    brands = find_brand(t)
    if brands:
        b = brands[0]
        items = load_stock()[b]
        lines = [f"• {no}: {q} pcs (₹{r})" for no, q, r in items[:25]]
        more = f"\n...aur {len(items)-25} items" if len(items) > 25 else ""
        return send(sender, f"📦 {b} stock:\n" + "\n".join(lines) + more)

    # ITEM NUMBER inquiry
    hits = find_item(t)
    if hits:
        lines = [f"• {b} - {no}: {q} pcs (₹{r})" for b, no, q, r in hits]
        return send(sender, "📦 Stock:\n" + "\n".join(lines))

    send(sender, "Nahi mila. Brand ka naam ya item number check karein, ya *menu* likhein.")

# ---------- Webhook ----------
@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge"), 200
    return "forbidden", 403

@app.route("/webhook", methods=["POST"])
def incoming():
    try:
        value = request.get_json()["entry"][0]["changes"][0]["value"]
        for m in value.get("messages", []):
            sender = m["from"]
            if sender in IGNORE_NUMBERS or sender in OWNER_NUMBERS:
                continue
            if m["type"] == "text":
                handle(sender, m["text"]["body"])
    except Exception as e:
        print("error:", e)
    return "ok", 200

@app.route("/")
def home():
    return "Bot running"
