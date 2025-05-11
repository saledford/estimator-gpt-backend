from fastapi import FastAPI, Request, UploadFile, File 
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import os
import requests
from dotenv import load_dotenv

# Load .env variables
load_dotenv()

app = FastAPI()

# Allow frontend (localhost:5173) to call backend (localhost:8000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Root route
@app.get("/")
def read_root():
    return {"status": "ok", "message": "Estimator GPT backend is running"}

# Save profile route (Zapier)
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

# File parsing endpoint
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
