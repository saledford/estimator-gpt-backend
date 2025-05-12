from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from typing import List
import fitz  # PyMuPDF

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"message": "Estimator GPT backend is running"}

master_scopes = {
    1: ("Sitework", ["grading", "site clearing", "erosion", "earthwork"]),
    2: ("Concrete", ["slab", "concrete", "footing"]),
    3: ("Masonry", ["cmu", "brick", "masonry"]),
    4: ("Metals", ["steel", "beam", "weld", "metal deck"]),
    5: ("Woods & Plastics", ["lumber", "wood framing", "sheathing", "blocking"]),
    6: ("Thermal & Moisture", ["insulation", "vapor barrier", "membrane", "flashing"]),
    7: ("Doors & Windows", ["door", "frame", "hardware", "window"]),
    8: ("Finishes", ["paint", "tile", "carpet", "acoustical", "flooring"]),
    9: ("Specialties", ["toilet accessory", "fire extinguisher", "lockers"]),
    10: ("Equipment", ["equipment", "furnish", "appliance"]),
    11: ("Furnishings", ["furniture", "casework", "countertop"]),
    12: ("Plumbing", ["pipe", "fixture", "sanitary", "pvc"]),
    13: ("HVAC", ["hvac", "duct", "vent", "air handler"]),
    14: ("Electrical", ["wire", "panel", "circuit", "lighting", "breaker"]),
    15: ("Fire Protection", ["sprinkler", "alarm", "fire suppression"]),
}

@app.post("/api/parse-structured")
async def parse_structured(files: List[UploadFile] = File(...)):
    found_scopes = set()
    full_text = ""

    for file in files:
        content = await file.read()
        doc = fitz.open(stream=content, filetype="pdf")
        for page in doc:
            full_text += page.get_text().lower()
        doc.close()

    parsed_quotes = []
    for scope_id, (scope_title, keywords) in master_scopes.items():
        match_found = any(k in full_text for k in keywords)
        parsed_quotes.append({
            "id": scope_id,
            "title": scope_title,
            "detail": f"{'Matched' if match_found else 'Not found'}: {', '.join(keywords)}"
        })

    return {"quotes": parsed_quotes}

@app.post("/api/update-quote")
async def update_quote(payload: dict):
    quote_id = payload.get("quote_id")
    action = payload.get("action")
    print(f"Quote {quote_id} marked as '{action}'")
    return {"message": f"Quote {quote_id} marked as '{action}'"}
@app.post("/api/parse-takeoff")
async def parse_takeoff(files: List[UploadFile] = File(...)):
    import re
    parsed_items = []

    for file in files:
        content = await file.read()
        doc = fitz.open(stream=content, filetype="pdf")

        for page in doc:
            lines = page.get_text().splitlines()
            for line in lines:
                # Try to match a quantity table line like "RB-1 Wall Base 721.0 LF"
                match = re.search(r"(\w+-?\w*)\s+(.+?)\s+(\d+[\d.,]*)\s+(EA|LF|SF|CY|PR)", line, re.IGNORECASE)
                if match:
                    code = match.group(1)
                    description = match.group(2).strip()
                    quantity = match.group(3).replace(",", "")
                    unit = match.group(4).upper()
                    parsed_items.append({
                        "code": code,
                        "description": description,
                        "quantity": quantity,
                        "unit": unit
                    })
        doc.close()

    return {"takeoff": parsed_items}