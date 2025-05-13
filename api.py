from fastapi import FastAPI, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from typing import List
from pydantic import BaseModel
import fitz  # PyMuPDF
import re
import openai
import os
from dotenv import load_dotenv

load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")

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
    suggested_name = ""

    for file in files:
        content = await file.read()
        doc = fitz.open(stream=content, filetype="pdf")

        for page in doc:
            full_text += page.get_text().lower()

        if doc.page_count > 0 and not suggested_name:
            first_page = doc.load_page(0)
            raw_text = first_page.get_text().strip()
            lines = raw_text.splitlines()

            candidates = []
            for line in lines:
                clean = line.strip()
                if clean.isupper() and not any(k in clean.lower() for k in ["project", "renovation", "public works", "drawings", "school", "improvements"]):
                    continue
                if len(clean) > 25 and not clean.lower().startswith(("jkf", "drawing", "project number")):
                    candidates.append(clean)

            priority_keywords = ["drawings for", "renovation", "public works", "school", "addition", "project", "improvements"]
            for candidate in candidates:
                if any(keyword in candidate.lower() for keyword in priority_keywords):
                    suggested_name = candidate
                    break

            if not suggested_name and candidates:
                suggested_name = candidates[0]

            if not suggested_name:
                suggested_name = file.filename.replace(".pdf", "").replace("_", " ").replace("-", " ").strip()

        doc.close()

    parsed_quotes = []
    for scope_id, (scope_title, keywords) in master_scopes.items():
        match_found = any(k in full_text for k in keywords)
        parsed_quotes.append({
            "id": scope_id,
            "title": scope_title,
            "detail": f"{'Matched' if match_found else 'Not found'}: {', '.join(keywords)}"
        })

    return {
        "quotes": parsed_quotes,
        "suggestedProjectName": suggested_name
    }

@app.post("/api/parse-takeoff")
async def parse_takeoff(files: List[UploadFile] = File(...)):
    parsed_items = []

    for file in files:
        content = await file.read()
        doc = fitz.open(stream=content, filetype="pdf")

        for page in doc:
            lines = page.get_text().splitlines()
            for line in lines:
                match = re.search(r"(\\w+-?\\w*)\\s+(.+?)\\s+(\\d+[\\d.,]*)\\s+(EA|LF|SF|CY|PR)", line, re.IGNORECASE)
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

class ChatRequest(BaseModel):
    discussion: list
    project_data: dict

@app.post("/api/chat")
async def chat(request: ChatRequest):
    messages = [
        {"role": "system", "content": "You are Estimator GPT, a professional construction estimator. Be clear, concise, and helpful."}
    ]

    for msg in request.discussion:
        role = "user" if msg["sender"] == "User" else "assistant"
        messages.append({"role": role, "content": msg["text"]})

    scopes = request.project_data.get("quotes", [])
    takeoff = request.project_data.get("takeoff", [])

    if scopes or takeoff:
        scope_text = "\\n".join(f"{q['title']}: {q.get('summary', '')}" for q in scopes)
        takeoff_text = "\\n".join(f"{t['trade']} – Qty: {t['quantity']} {t['unit']}" for t in takeoff)
        messages.append({
            "role": "system",
            "content": f"Scopes:\\n{scope_text}\\n\\nTakeoff:\\n{takeoff_text}"
        })

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=messages,
            temperature=0.4
        )
        reply = response.choices[0].message.content.strip()
    except Exception as e:
        reply = f"Error generating response: {str(e)}"

    return {"reply": reply}
