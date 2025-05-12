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

@app.post("/api/parse-structured")
async def parse_structured(files: List[UploadFile] = File(...)):
    parsed_quotes = []
    seen_scopes = set()

    for file in files:
        content = await file.read()
        doc = fitz.open(stream=content, filetype="pdf")

        full_text = ""
        for page in doc:
            full_text += page.get_text().lower()

        doc.close()

        # Add scope checks here
        def add_scope(id, title, detail):
            if title not in seen_scopes:
                parsed_quotes.append({"id": id, "title": title, "detail": detail})
                seen_scopes.add(title)

        if "door" in full_text or "hm" in full_text or "hardware" in full_text:
            add_scope(1, "Doors and Hardware", "Matched line with 'door' or 'HM'.")
        if "slab" in full_text or "concrete" in full_text:
            add_scope(2, "Slab Concrete", "Matched line with 'slab' or 'concrete'.")
        if "paint" in full_text or "latex" in full_text:
            add_scope(3, "Painting", "Matched line with 'paint' or 'latex'.")
        if "gwb" in full_text or "drywall" in full_text:
            add_scope(4, "Drywall Package", "Matched line with 'drywall' or 'gwb'.")
        if "masonry" in full_text or "cmu" in full_text:
            add_scope(5, "Masonry", "Matched line with 'masonry' or 'CMU'.")
        if "roof" in full_text or "truss" in full_text:
            add_scope(6, "Roof Framing", "Matched line with 'roof' or 'truss'.")
        if "hvac" in full_text or "duct" in full_text:
            add_scope(7, "HVAC System", "Matched line with 'hvac' or 'duct'.")
        if "fire sprinkler" in full_text or "sprinkler" in full_text:
            add_scope(8, "Fire Protection", "Matched line with 'sprinkler'.")
        if "stud" in full_text or "metal stud" in full_text:
            add_scope(9, "Wall Framing", "Matched line with 'metal stud'.")
        if "ceiling tile" in full_text or "acoustical" in full_text:
            add_scope(10, "Ceiling System", "Matched line with 'ceiling tile' or 'acoustical'.")

    if not parsed_quotes:
        parsed_quotes.append({"id": 99, "title": "No Trade Detected", "detail": "No known scope keywords matched."})

    return {"quotes": parsed_quotes}

@app.post("/api/update-quote")
async def update_quote(payload: dict):
    quote_id = payload.get("quote_id")
    action = payload.get("action")
    print(f"Quote {quote_id} marked as '{action}'")
    return {"message": f"Quote {quote_id} marked as '{action}'"}
