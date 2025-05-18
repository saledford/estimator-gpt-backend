from fastapi import FastAPI, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from typing import List
from pydantic import BaseModel
import fitz  # PyMuPDF
import re
import os
import uuid
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
                return {"message": "File deleted successfully"}
    return {"detail": "File not found"}

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

    quotes = [
        {
            "id": div,
            "title": name,
            "summary": "Placeholder for GPT",
            "cost": 0,
            "markup": 10,
            "finalPrice": 0
        } for div, name in CSI_DIVISIONS
    ]

    return {
        "quotes": quotes,
        "suggestedProjectName": suggested_name
    }

@app.post("/api/generate-summary")
async def generate_summary(files: List[UploadFile] = File(...)):
    full_text = ""
    for file in files:
        content = await file.read()
        doc = fitz.open(stream=content, filetype="pdf")
        for page in doc:
            full_text += page.get_text()
        doc.close()

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": "You are an experienced architect. Read the project documentation and provide a detailed project summary suitable for a general contractor. Focus on the overall design intent, materials, finishes, and unique features."
                },
                {
                    "role": "user",
                    "content": full_text[:12000]
                }
            ],
            temperature=0.5
        )
        reply = response.choices[0].message.content.strip()
        return {"summary": reply}
    except Exception as e:
        return {"summary": f"Error generating summary: {str(e)}"}

@app.post("/api/parse-specs")
async def parse_specs(files: List[UploadFile] = File(...)):
    full_text = ""
    for file in files:
        content = await file.read()
        doc = fitz.open(stream=content, filetype="pdf")
        for page in doc:
            full_text += page.get_text()
        doc.close()

    try:
        prompt = """You are a construction estimator. Read the project manual below and generate a brief but specific summary for each of the following CSI divisions. Use only content from the manual. If no information is found for a division, write 'Not specified.' Format the response as JSON:

{
  '03': 'Concrete summary here...',
  '09': 'Finishes summary here...',
  '22': 'Plumbing summary here...'
}

Manual:
""" + full_text[:12000]

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You extract division summaries from construction manuals."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.4
        )
        reply = response.choices[0].message.content.strip()
        return {"divisionDescriptions": reply}
    except Exception as e:
        return {"divisionDescriptions": f"Error: {str(e)}"}

@app.post("/api/analyze-division/{division_id}")
async def analyze_division(division_id: str, request: Request):
    body = await request.json()
    quotes = body.get("quotes", [])
    takeoff = body.get("takeoff", [])
    specs = body.get("specs", {})

    division_quote = next((q for q in quotes if q["id"] == division_id), None)
    division_takeoff = [t for t in takeoff if t["division"] == division_id]
    division_specs = specs.get(division_id, "")

    summary_text = division_quote.get("summary", "") if division_quote else ""

    prompt = f"""
Analyze Division {division_id} in this construction estimate.

Quote Summary:
{summary_text or 'None provided'}

Specs:
{division_specs or 'Not specified'}

Takeoff Items:
"""
    for t in division_takeoff:
        line = f"- {t['description']} | Qty: {t['quantity']} {t['unit']} @ ${t['unitCost']}"
        prompt += f"{line}\n"

    prompt += """

Provide a short analysis:
- What scope is clearly covered?
- What appears to be missing?
- Any unusual or risky exclusions?
Be concise and accurate.
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a construction estimator reviewing one division in detail."},
                {"role": "user", "content": prompt[:12000]}
            ],
            temperature=0.4
        )
        reply = response.choices[0].message.content.strip()
        return {"analysis": reply}
    except Exception as e:
        return {"analysis": f"Error: {str(e)}"}
