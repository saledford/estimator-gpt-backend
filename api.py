from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
import shutil
import os
import sqlite3
from pdf_parser import extract_structured_takeoff

app = FastAPI()

# === CORS Middleware ===
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all for development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === SQLite Database Connection ===
def get_db_connection():
    conn = sqlite3.connect("quotes.db")
    conn.row_factory = sqlite3.Row
    return conn

# === Save user profile ===
@app.post("/api/save-profile")
async def save_profile(profile: dict):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO user_profiles (name, company, email, region, labor_rates_json, user_tier, onboarding_completed)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        profile["name"],
        profile["company"],
        profile["email"],
        profile["region"],
        profile["labor_rates_json"],
        profile.get("user_tier", "Free"),
        profile.get("onboarding_completed", False),
    ))
    conn.commit()
    conn.close()
    return {"message": "Profile saved"}

# === Upload & Parse PDF (Structured Takeoff e.g. Door Schedule) ===
@app.post("/api/parse-structured")
async def parse_structured(file: UploadFile = File(...)):
    temp_path = f"temp_uploads/{file.filename}"
    os.makedirs("temp_uploads", exist_ok=True)

    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        results = extract_structured_takeoff(temp_path)
        return {"parsed": results}
    finally:
        os.remove(temp_path)
