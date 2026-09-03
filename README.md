# FACI Backend

Python/Flask backend for the FACI donation website.

## Files

- `app.py` — Flask API server
- `requirements.txt` — Python dependencies
- `render.yaml` — Render deployment configuration
- `.gitignore` — prevents local secrets/database files from being committed

## Render settings

The included `render.yaml` uses:

Build command:
`pip install -r requirements.txt`

Start command:
`gunicorn app:app`

Set these Render Environment Variables:

- `FLW_SECRET_KEY` = your Flutterwave secret key
- `FLW_PUBLIC_KEY` = your Flutterwave public key
- `CURRENCY` = `NGN`

Do NOT put the Flutterwave secret key in GitHub.

## API routes

- `GET /api/config`
- `GET /api/donations/total`
- `POST /api/flutterwave/verify`
- `POST /api/bank-transfer-receipt`
- `POST /api/chat`

The backend enables CORS for `/api/*` so a static frontend hosted separately can call it.

IMPORTANT:
The backend is intentionally separate from the existing website files. It does not modify the website HTML/CSS/JavaScript.
