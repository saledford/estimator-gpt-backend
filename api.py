from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import os
import requests
from dotenv import load_dotenv

# Load .env variables
load_dotenv()

app = FastAPI()

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
