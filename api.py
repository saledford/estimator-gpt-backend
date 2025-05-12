
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from typing import List

app = FastAPI()

# Enable CORS so frontend (localhost:5173) can connect
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"message": "Estimator GPT backend is running"}

@app.post("/api/parse-structured")
async def parse_structured(files: List[UploadFile] = File(...)):
    parsed_quotes = []

    for file in files:
        content = await file.read()
        text = content.decode("utf-8", errors="ignore").lower()

        if "door" in text:
            parsed_quotes.append({
                "id": 1,
                "title": "Doors and Hardware",
                "detail": "Found 'door' keyword in file."
            })
        if "slab" in text:
            parsed_quotes.append({
                "id": 2,
                "title": "Slab Concrete",
                "detail": "Found 'slab' keyword in file."
            })
        if "paint" in text:
            parsed_quotes.append({
                "id": 3,
                "title": "Painting",
                "detail": "Found 'paint' keyword in file."
            })
        if "gwb" in text or "drywall" in text:
            parsed_quotes.append({
                "id": 4,
                "title": "Drywall Package",
                "detail": "Found 'drywall' keyword in file."
            })

    if not parsed_quotes:
        parsed_quotes.append({
            "id": 99,
            "title": "No Trade Detected",
            "detail": "No known keywords found."
        })

    return {"quotes": parsed_quotes}

@app.post("/api/update-quote")
async def update_quote(payload: dict):
    quote_id = payload.get("quote_id")
    action = payload.get("action")
    print(f"Quote {quote_id} marked as '{action}'")
    return {"message": f"Quote {quote_id} marked as '{action}'"}
