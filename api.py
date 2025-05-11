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
import re

# Load .env variables
load_dotenv()

app = FastAPI()

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
        all_lines = []
        for page in doc:
            page_text = page.get_text()
            lines = page_text.splitlines()
            all_lines.extend(lines)

        found_quotes = []
        seen = set()

        # === DOOR DETECTION ===
        door_keywords = ["door", "doors", "hm", "hollow metal", "flush", "frame"]
        door_qty_pattern = re.compile(r"\(?\b(\d{1,3})\b[\)]?\s?(ea|each|doors?)?", re.IGNORECASE)

        for line in all_lines:
            lower_line = line.lower()
            if "doors and hardware" in seen:
                continue

            if any(kw in lower_line for kw in door_keywords):
                qty_match = door_qty_pattern.search(line)
                seen.add("doors and hardware")
                if qty_match:
                    qty = qty_match.group(1)
                    found_quotes.append({
                        "id": len(found_quotes) + 1,
                        "title": "Doors and Hardware",
                        "detail": f"Found '{line.strip()}' → Quantity: {qty}"
                    })
                else:
                    found_quotes.append({
                        "id": len(found_quotes) + 1,
                        "title": "Doors and Hardware",
                        "detail": f"Matched line but no quantity found: \"{line.strip()}\""
                    })

        # === SLAB SF DETECTION ===
        slab_keywords = ["slab", "concrete", "flatwork", "footing"]
        sf_pattern = re.compile(r"\b([\d,]{2,7})\s?(sf|square feet)\b", re.IGNORECASE)

        for line in all_lines:
            lower_line = line.lower()
            if "slab concrete" in seen:
                continue

            if any(kw in lower_line for kw in slab_keywords):
                sf_match = sf_pattern.search(line)
                seen.add("slab concrete")
                if sf_match:
                    sf = sf_match.group(1)
                    found_quotes.append({
                        "id": len(found_quotes) + 1,
                        "title": "Slab Concrete",
                        "detail": f"Found '{line.strip()}' → Area: {sf} SF"
                    })
                else:
                    found_quotes.append({
                        "id": len(found_quotes) + 1,
                        "title": "Slab Concrete",
                        "detail": f"Matched line but no square footage found: \"{line.strip()}\""
                    })

        return {
            "filename": file.filename,
            "quotes": found_quotes
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
