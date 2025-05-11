from fastapi import FastAPI, UploadFile, File
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os
import requests
import sqlite3
from datetime import datetime
from dotenv import load_dotenv
import fitz  # PyMuPDF

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

# === MODELS ===

class QuoteFeedback(BaseModel):
    quote_id: int
    action: str

# === ROUTES ===

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Estimator GPT backend is running"}

@app.post("/api/parse-pdf")
async def parse_pdf(file: UploadFile = File(...)):
    try:
        contents = await file.read()

        with open("temp.pdf", "wb") as f:
            f.write(contents)

        doc = fitz.open("temp.pdf")
        text = ""
        for page in doc:
            text += page.get_text()

        # Very basic quote detection logic
        quote_items = []
        keywords = {
            "door": "Doors and Hardware",
            "hardware": "Doors and Hardware",
            "slab": "Slab Concrete",
            "concrete": "Slab Concrete",
            "drywall": "Drywall Package",
        }

        found = set()
        for keyword, label in keywords.items():
            if keyword in text.lower() and label not in found:
                found.add(label)
                quote_items.append({
                    "id": len(quote_items) + 1,
                    "title": label,
                    "detail": f"Found keyword '{keyword}' in file"
                })

        return {
            "filename": file.filename,
            "quotes": quote_items
        }

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

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
