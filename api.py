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

class QuoteFeedback(BaseModel):
    quote_id: int
    action: str

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

        # Pattern definitions
        door_qty_pattern = re.compile(r"\(?\b(\d{1,3})\b[\)]?\s?(ea|each|doors?)?", re.IGNORECASE)
        sf_pattern = re.compile(r"\b([\d,]{2,7})\s?(sf|square feet)\b", re.IGNORECASE)
        lf_pattern = re.compile(r"\b([\d,]{2,7})\s?(lf|linear feet)\b", re.IGNORECASE)

        scope_definitions = [
            {
                "title": "Doors and Hardware",
                "keywords": ["door", "doors", "hardware", "hm", "frame"],
                "pattern": door_qty_pattern,
                "type": "count"
            },
            {
                "title": "Slab Concrete",
                "keywords": ["slab", "concrete", "flatwork", "footing"],
                "pattern": sf_pattern,
                "type": "square feet"
            },
            {
                "title": "Drywall Package",
                "keywords": ["drywall", "gwb", "gypsum", "ceiling", "wallboard"],
                "pattern": sf_pattern,
                "type": "square feet"
            },
            {
                "title": "Framing",
                "keywords": ["metal stud", "framing", "partition"],
                "pattern": lf_pattern,
                "type": "linear feet"
            },
            {
                "title": "Paint",
                "keywords": ["paint", "coating", "painted wall"],
                "pattern": sf_pattern,
                "type": "square feet"
            }
        ]

        for line in all_lines:
            lower_line = line.lower()

            for scope in scope_definitions:
                title = scope["title"]
                if title in seen:
                    continue

                if any(kw in lower_line for kw in scope["keywords"]):
                    seen.add(title)
                    match = scope["pattern"].search(line)
                    if match:
                        qty = match.group(1)
                        found_quotes.append({
                            "id": len(found_quotes) + 1,
                            "title": title,
                            "detail": f"Found '{line.strip()}' → Quantity: {qty} {scope['type']}"
                        })
                    else:
                        found_quotes.append({
                            "id": len(found_quotes) + 1,
                            "title": title,
                            "detail": f"Matched line but no quantity found: \"{line.strip()}\""
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
