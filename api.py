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

CSI_KEYWORDS = {
    "01": ["general requirement", "mobilization"],
    "03": ["concrete", "slab", "footing"],
    "04": ["masonry", "brick", "block", "cmu"],
    "05": ["steel", "metal deck", "joist"],
    "06": ["wood", "framing", "carpentry"],
    "07": ["insulation", "roof", "moisture", "flashing"],
    "08": ["door", "window", "frame", "hardware"],
    "09": ["drywall", "paint", "ceiling", "flooring", "tile", "acoustical"],
    "10": ["toilet accessory", "specialty", "locker"],
    "11": ["equipment", "kitchen hood"],
    "12": ["furniture", "casework", "countertop"],
    "13": ["pre-engineered building", "dome"],
    "14": ["elevator", "lift"],
    "21": ["sprinkler", "fire suppression"],
    "22": ["plumbing", "pipe", "fixture"],
    "23": ["hvac", "duct", "air handler"],
    "26": ["electrical", "conduit", "lighting", "panel"]
}

@app.post("/api/parse-takeoff")
async def parse_takeoff(files: List[UploadFile] = File(...)):
    parsed_items = []

    regex_patterns = [
        re.compile(r"(\w+-?\w*)\s+(.+?)\s+(\d+[\d.,]*)\s+(EA|LF|SF|CY|PR)", re.IGNORECASE),
        re.compile(r"(.+?)\s+(\d+[\d.,]*)\s+(EA|LF|SF|CY|PR)", re.IGNORECASE)  # Fallback: no code
    ]

    for file in files:
        content = await file.read()
        doc = fitz.open(stream=content, filetype="pdf")

        for page in doc:
            lines = page.get_text().splitlines()
            for line in lines:
                line = line.strip()
                if not line or len(line.split()) < 3:
                    continue

                match = None
                for pattern in regex_patterns:
                    match = pattern.match(line)
                    if match:
                        break

                if match:
                    groups = match.groups()
                    if len(groups) == 4:
                        code, description, quantity, unit = groups
                    else:
                        code = "N/A"
                        description, quantity, unit = groups

                    description = description.strip()
                    quantity = quantity.replace(",", "")
                    unit = unit.upper()

                    division = "Unknown"
                    for div, keywords in CSI_KEYWORDS.items():
                        if any(kw in description.lower() for kw in keywords) or any(kw in code.lower() for kw in keywords):
                            division = div
                            break

                    issue = None
                    try:
                        float(quantity)
                    except ValueError:
                        issue = "Invalid quantity format"

                    parsed_items.append({
                        "division": division,
                        "description": description,
                        "quantity": quantity,
                        "unit": unit,
                        "unitCost": 0,
                        "modifier": 0,
                        "customTotal": None,
                        "useCustomTotal": False,
                        "issue": issue
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
        scope_text = "\n".join(f"{q['title']}: {q.get('summary', '')}" for q in scopes)
        takeoff_text = "\n".join(f"{t['division']} {t['description']} – Qty: {t['quantity']} {t['unit']} @ ${t['unitCost']}" for t in takeoff)
        messages.append({
            "role": "system",
            "content": f"Scopes:\n{scope_text}\n\nTakeoff:\n{takeoff_text}"
        })

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=0.4
        )
        reply = response.choices[0].message.content.strip()
    except Exception as e:
        reply = f"Error generating response: {str(e)}"

    return {"reply": reply}
