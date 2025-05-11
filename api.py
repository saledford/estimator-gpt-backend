from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os
import requests
import sqlite3
from datetime import datetime
from dotenv import load_dotenv

# Load .env variables
load_dotenv()

app = FastAPI()

# Enable frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# === DATABASE SETUP ===
DB_FILE = "quotes.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS quote_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quote_id INTEGER,
            action TEXT,
            action_timestamp TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# === API ROUTES ===

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Estimator GPT backend is running"}

@app.post("/api/save-profile")
async def save_profile(payload: dict):
    try:
        user_name = payload.get("name", "Unknown")
        profile_id = payload.get("id", "No ID")

        webhook_url = os.getenv("ZAPIER_WEBHOOK_URL")
        if not webhook_url:
            return JSONResponse(status_code=500, content={"error": "Zapier webhook URL not configured"})

        response = requests.post(webhook_url, json=payload)

        if response.status_code != 200:
            return JSONResponse(status_code=502, content={"error": "Failed to send webhook", "details": response.text})

        print(f"✅ Sent webhook to Zapier for profile {profile_id}: {response.status_code}")
        return {"message": f"Saved profile for {user_name} with ID {profile_id}"}

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": "Internal server error", "details": str(e)})

@app.post("/api/parse-pdf")
async def parse_pdf(file: UploadFile = File(...)):
    return {
        "filename": file.filename,
        "quotes": [
            {"id": 1, "title": "Doors and Hardware", "detail": "Qty: 27 doors, 5 frames"},
            {"id": 2, "title": "Slab Concrete", "detail": "Total SF: 3,240 SF"},
            {"id": 3, "title": "Drywall Package", "detail": "Walls: 1,500 SF, Ceilings: 800 SF"}
        ]
    }

# === NEW: Quote Feedback Endpoint ===

class QuoteFeedback(BaseModel):
    quote_id: int
    action: str

@app.post("/api/update-quote")
async def update_quote(feedback: QuoteFeedback):
    try:
        print(f"📩 Received feedback: quote_id={feedback.quote_id}, action={feedback.action}")
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        timestamp = datetime.utcnow().isoformat()
        cursor.execute(
            "INSERT INTO quote_feedback (quote_id, action, action_timestamp) VALUES (?, ?, ?)",
            (feedback.quote_id, feedback.action, timestamp)
        )
        conn.commit()
        conn.close()
        print("✅ Feedback saved to database.")
        return {"message": f"Quote {feedback.quote_id} set to '{feedback.action}'"}
    except Exception as e:
        print("❌ Error saving feedback:", str(e))
        return JSONResponse(status_code=500, content={"error": str(e)})


