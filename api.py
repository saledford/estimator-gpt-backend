from fastapi import FastAPI, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from typing import List
from pydantic import BaseModel
import fitz  # PyMuPDF
import re
import os
import uuid
import logging
from dotenv import load_dotenv
from openai import OpenAI
import json

load_dotenv()
client = OpenAI()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(filename="quote_parsing.log", level=logging.INFO, format="%(asctime)s - %(message)s")

@app.get("/")
def root():
    return {"message": "Estimator GPT backend is running"}

UPLOAD_FOLDER = "temp_uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

@app.post("/api/upload-file")
async def upload_file(file: UploadFile = File(...)):
    file_id = str(uuid.uuid4())
    filename = f"{file_id}_{file.filename}"
    filepath = os.path.join(UPLOAD_FOLDER, filename)

    try:
        with open(filepath, "wb") as f:
            content = await file.read()
            f.write(content)
        logging.info(f"Uploaded file: {filename}")
        return {"fileId": file_id}
    except Exception as e:
        return {"detail": f"Failed to save file {file.filename}: {str(e)}"}

@app.get("/api/get-file/{file_id}")
async def get_file(file_id: str):
    for filename in os.listdir(UPLOAD_FOLDER):
        if filename.startswith(file_id):
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            if os.path.exists(filepath):
                return FileResponse(filepath, media_type="application/pdf", filename=filename)
    return {"detail": "File not found"}

@app.delete("/api/delete-file/{file_id}")
async def delete_file(file_id: str):
    for filename in os.listdir(UPLOAD_FOLDER):
        if filename.startswith(file_id):
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            if os.path.exists(filepath):
                os.remove(filepath)
                logging.info(f"Deleted file: {filename}")
                return {"message": "File deleted successfully"}
    return {"detail": "File not found"}

@app.post("/api/extract-text")
async def extract_text(files: List[UploadFile] = File(...)):
    full_text = ""
    for file in files:
        content = await file.read()
        doc = fitz.open(stream=content, filetype="pdf")
        for page in doc:
            full_text += page.get_text()
        doc.close()
    return {"text": full_text}

CSI_DIVISIONS = [
    ("01", "General Requirements"),
    ("02", "Existing Conditions"),
    ("03", "Concrete"),
    ("04", "Masonry"),
    ("05", "Metals"),
    ("06", "Wood, Plastics, and Composites"),
    ("07", "Thermal and Moisture Protection"),
    ("08", "Openings (Doors, Windows)"),
    ("09", "Finishes"),
    ("10", "Specialties"),
    ("11", "Equipment"),
    ("12", "Furnishings"),
    ("13", "Special Construction"),
    ("14", "Conveying Equipment (Elevators)"),
    ("21", "Fire Suppression"),
    ("22", "Plumbing"),
    ("23", "HVAC"),
    ("25", "Integrated Automation"),
    ("26", "Electrical"),
    ("27", "Communications"),
    ("28", "Electronic Safety and Security"),
    ("31", "Earthwork"),
    ("32", "Exterior Improvements"),
    ("33", "Utilities")
]

DIVISION_KEYWORDS = {
    "03": ["concrete", "slab", "footing"],
    "08": ["door", "window", "glazing"],
    "09": ["paint", "drywall", "gypsum", "finish", "flooring"],
    "22": ["plumbing", "fixture", "pipe"],
    "23": ["hvac", "mechanical", "ventilation", "air handler"],
    "26": ["electrical", "receptacle", "lighting", "panel"]
}

@app.post("/api/parse-structured")
async def parse_structured(files: List[UploadFile] = File(...)):
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

    quotes = []
    for div, name in CSI_DIVISIONS:
        summary = "Not detected"
        for keyword in DIVISION_KEYWORDS.get(div, []):
            if keyword in full_text:
                summary = f"{name} scope detected based on presence of '{keyword}'"
                break
        quotes.append({
            "id": div,
            "title": name,
            "summary": summary,
            "cost": 0,
            "markup": 10,
            "finalPrice": 0
        })

    logging.info(f"Structured parsing completed. Suggested project name: {suggested_name}")
    return {
        "quotes": quotes,
        "suggestedProjectName": suggested_name
    }
