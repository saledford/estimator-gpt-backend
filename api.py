from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import List
import os
import uuid
import shutil
import fitz  # PyMuPDF
import re

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

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

@app.post("/api/upload-file")
async def upload_file(file: UploadFile = File(...)):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed.")
    file_id = str(uuid.uuid4())
    file_path = os.path.join(UPLOAD_DIR, file_id + ".pdf")
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"fileId": file_id}

@app.get("/api/get-file/{file_id}")
async def get_file(file_id: str):
    file_path = os.path.join(UPLOAD_DIR, file_id + ".pdf")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(file_path, media_type="application/pdf")

@app.delete("/api/delete-file/{file_id}")
async def delete_file(file_id: str):
    file_path = os.path.join(UPLOAD_DIR, file_id + ".pdf")
    if os.path.exists(file_path):
        os.remove(file_path)
        return {"message": "File deleted."}
    raise HTTPException(status_code=404, detail="File not found.")

@app.post("/api/parse-structured")
async def parse_structured(files: List[UploadFile] = File(...)):
    full_text = ""
    for file in files:
        content = await file.read()
        doc = fitz.open(stream=content, filetype="pdf")
        for page in doc:
            full_text += page.get_text().lower()
        doc.close()

    # Try to extract project name
    project_name_match = re.search(r"(?i)(project(?: name)?|construction documents for)[:\-\n]?\s*(.+)", full_text)
    suggested_project_name = "Unnamed Project"
    if project_name_match:
        suggested_project_name = project_name_match.group(2).strip().split("\n")[0]

    parsed_quotes = []
    for scope_id, (scope_title, keywords) in master_scopes.items():
        matched_keywords = [k for k in keywords if k in full_text]
        match_found = len(matched_keywords) > 0
        summary_text = ""

        if match_found:
            snippets = []
            for kw in matched_keywords:
                match = re.search(rf".{{0,60}}{re.escape(kw)}.{{0,60}}", full_text)
                if match:
                    snippets.append(match.group().strip())
            preview = "; ".join(snippets[:3])
            summary_text = f"{scope_title} scope includes: {preview}"

        parsed_quotes.append({
            "id": scope_id,
            "title": scope_title,
            "detail": f"{'Matched' if match_found else 'Not found'}: {', '.join(keywords)}",
            "summary": summary_text
        })

    return {
        "quotes": parsed_quotes,
        "suggestedProjectName": suggested_project_name
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
