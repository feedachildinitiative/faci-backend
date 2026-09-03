import os
import sqlite3
import uuid
from datetime import datetime, timezone
from functools import wraps

import requests
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)

# GitHub Pages -> Render requires CORS because the frontend and backend
# are normally on different origins.
CORS(app, resources={r"/api/*": {"origins": "*"}})

PORT = int(os.environ.get("PORT", "10000"))
DB_PATH = os.environ.get("DATABASE_PATH", "/tmp/faci_donations.db")
FLW_SECRET_KEY = os.environ.get("FLW_SECRET_KEY", "")
FLW_PUBLIC_KEY = os.environ.get("FLW_PUBLIC_KEY", "")
CURRENCY = os.environ.get("CURRENCY", "NGN")

# Optional: comma-separated origins. Leave unset to allow the existing
# frontend to work without changing its origin.
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "").strip()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS donations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id TEXT UNIQUE,
            amount REAL NOT NULL,
            currency TEXT NOT NULL,
            name TEXT,
            email TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bank_transfer_receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            email TEXT,
            amount REAL,
            reference TEXT,
            receipt_name TEXT,
            receipt_data BLOB,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


@app.after_request
def add_cors_headers(response):
    if ALLOWED_ORIGINS:
        origin = request.headers.get("Origin")
        allowed = [x.strip() for x in ALLOWED_ORIGINS.split(",") if x.strip()]
        if origin in allowed:
            response.headers["Access-Control-Allow-Origin"] = origin
    return response


def require_secret_key():
    if not FLW_SECRET_KEY:
        return jsonify({
            "status": "error",
            "message": "Flutterwave secret key is not configured on the server."
        }), 500
    return None


@app.get("/")
def home():
    return jsonify({
        "status": "success",
        "service": "FACI donation backend",
        "message": "FACI backend is running."
    })


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/api/config")
def config():
    # Only the public key is returned. The secret key is never exposed.
    return jsonify({
        "status": "success",
        "public_key": FLW_PUBLIC_KEY,
        "currency": CURRENCY
    })


@app.get("/api/donations/total")
def donations_total():
    conn = db()
    row = conn.execute("""
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM donations
        WHERE status = 'successful'
    """).fetchone()
    conn.close()

    return jsonify({
        "status": "success",
        "total": float(row["total"] or 0),
        "currency": CURRENCY
    })


def flutterwave_verify(transaction_id):
    response = requests.get(
        f"https://api.flutterwave.com/v3/transactions/{transaction_id}/verify",
        headers={
            "Authorization": f"Bearer {FLW_SECRET_KEY}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


@app.route("/api/flutterwave/verify", methods=["POST"])
def verify_flutterwave():
    missing = require_secret_key()
    if missing:
        return missing

    payload = request.get_json(silent=True) or request.form.to_dict()

    # Accept several common names so the existing donate page has a better
    # chance of working without changing its field names.
    transaction_id = (
        payload.get("transaction_id")
        or payload.get("transactionId")
        or payload.get("tx_ref")
        or payload.get("transaction")
        or payload.get("id")
    )

    if not transaction_id:
        return jsonify({
            "status": "error",
            "message": "A Flutterwave transaction ID is required."
        }), 400

    try:
        result = flutterwave_verify(transaction_id)
    except requests.RequestException as exc:
        return jsonify({
            "status": "error",
            "message": "Unable to contact Flutterwave.",
            "details": str(exc)
        }), 502

    data = result.get("data") or {}
    payment_status = str(data.get("status", "")).lower()
    amount = float(data.get("amount") or 0)
    currency = data.get("currency") or CURRENCY
    customer = data.get("customer") or {}

    # Only successful transactions are recorded.
    successful = payment_status == "successful"

    if successful:
        conn = db()
        try:
            conn.execute("""
                INSERT OR IGNORE INTO donations
                (transaction_id, amount, currency, name, email, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                str(transaction_id),
                amount,
                currency,
                customer.get("name"),
                customer.get("email"),
                "successful",
                datetime.now(timezone.utc).isoformat(),
            ))
            conn.commit()
        finally:
            conn.close()

    return jsonify({
        "status": "success" if successful else "failed",
        "verified": successful,
        "transaction_id": str(transaction_id),
        "amount": amount,
        "currency": currency,
        "payment_status": payment_status,
        "customer": {
            "name": customer.get("name"),
            "email": customer.get("email")
        },
        "flutterwave": result
    })


@app.route("/api/bank-transfer-receipt", methods=["POST"])
def bank_transfer_receipt():
    # Supports multipart/form-data and a normal form post.
    uploaded = (
        request.files.get("receipt")
        or request.files.get("file")
        or request.files.get("image")
    )

    form = request.form

    name = form.get("name") or form.get("full_name")
    email = form.get("email")
    amount_raw = form.get("amount")
    reference = form.get("reference") or form.get("transaction_reference")

    try:
        amount = float(amount_raw) if amount_raw not in (None, "") else None
    except ValueError:
        amount = None

    receipt_name = uploaded.filename if uploaded else None
    receipt_data = uploaded.read() if uploaded else None

    # Also accept a request with no actual file so the endpoint remains
    # compatible with simple frontend forms.
    if receipt_data is None:
        receipt_data = b""

    conn = db()
    conn.execute("""
        INSERT INTO bank_transfer_receipts
        (name, email, amount, reference, receipt_name, receipt_data, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        name, email, amount, reference, receipt_name, receipt_data,
        datetime.now(timezone.utc).isoformat(),
    ))
    conn.commit()
    receipt_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()

    return jsonify({
        "status": "success",
        "message": "Bank transfer receipt submitted successfully.",
        "receipt_id": receipt_id
    }), 201


@app.route("/api/chat", methods=["POST"])
def chat():
    # Keeps a harmless API endpoint available for frontend chat requests.
    # No external AI key is required. This can be extended later.
    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message") or "").strip()

    if not message:
        return jsonify({
            "status": "error",
            "message": "Please provide a message."
        }), 400

    return jsonify({
        "status": "success",
        "reply": "Thanks for contacting FACI. Please use the contact information on the website for assistance."
    })


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
