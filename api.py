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

                    # Try to map to a division based on keywords
                    division = "Unknown"
                    for div, keywords in CSI_KEYWORDS.items():
                        if any(kw in description.lower() for kw in keywords):
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
