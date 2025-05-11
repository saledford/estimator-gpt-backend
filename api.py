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
    allow_origins=["*"],  # You can change this to ["http://localhost:5173"] for tighter security
    allow_methods=["*"],
    allow_headers=["*"],
)

# Root route (for Render health checks)
@app.get("/")
def read_root():
    return {"status": "ok", "message": "Estimator GPT backend is running"}

# Save profile route
@app.post("/api/save-profile")
async def save_profile(payload: dict):
    try:
        user_name = payload.get("name", "Unknown")
        profile_id = payload.get("id", "No ID")

        webhook_url = os.getenv("Z_
