from fastapi import FastAPI, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from typing import List
import os
import uuid
import logging
import fitz  # PyMuPDF
import json
from dotenv import load_dotenv
from openai import OpenAI

# === Init ===
load_dotenv()
client = OpenAI()
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(
    filename="quote_parsing.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

CSI_DIVISIONS = {
    "01": "General Requirements", "02": "Existing Conditions", "03": "Concrete",
    "04": "Masonry", "05": "Metals", "06": "Wood, Plastics, and Composites",
    "07": "Thermal and Moisture Protection", "08": "Openings (Doors, Windows)",
    "09": "Finishes", "10": "Specialties", "11": "Equipment", "12": "Furnishings",
    "13": "Special Construction", "14": "Conveying Equipment (Elevators)", "21": "Fire Suppression",
    "22": "Plumbing", "23": "HVAC", "25": "Integrated Automation", "26": "Electrical",
    "27": "Communications", "28": "Electronic Safety and Security", "31": "Earthwork",
    "32": "Exterior Improvements", "33": "Utilities"
}

UPLOAD_DIR = "./temp_uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.get("/")
async def root():
    return {"message": "Estimator GPT backend is running"}

@app.post("/api/upload-file")
async def upload_file(file: UploadFile = File(...)):
    try:
        file_id = str(uuid.uuid4())
        path = os.path.join(UPLOAD_DIR, file_id)
        with open(path, "wb") as f:
            f.write(await file.read())
        logger.info(f"Uploaded file: {file.filename} as {file_id}")
        return {"fileId": file_id, "name": file.filename}
    except Exception as e:
        logger.error(f"Upload error: {str(e)}")
        return JSONResponse(status_code=500, content={"detail": f"Upload failed: {str(e)}"})

@app.get("/api/get-file/{file_id}")
async def get_file(file_id: str):
    path = os.path.join(UPLOAD_DIR, file_id)
    if not os.path.exists(path):
        return JSONResponse(status_code=404, content={"detail": "File not found"})
    return FileResponse(path, filename=file_id)

@app.delete("/api/delete-file/{file_id}")
async def delete_file(file_id: str):
    path = os.path.join(UPLOAD_DIR, file_id)
    if os.path.exists(path):
        os.remove(path)
        logger.info(f"Deleted file: {file_id}")
        return {"message": f"{file_id} deleted"}
    return JSONResponse(status_code=404, content={"detail": "File not found"})

# === Shared Utility ===
async def extract_text_from_files(files: List[UploadFile]) -> str:
    text_chunks = []
    for file in files:
        content = await file.read()
        if file.filename.lower().endswith(".pdf"):
            doc = fitz.open(stream=content, filetype="pdf")
            for page in doc:
                text_chunks.append(page.get_text())
            doc.close()
        else:
            text_chunks.append(content.decode("utf-8", errors="ignore"))
        logger.info(f"Parsed file: {file.filename}")
    full_text = "\n\n".join(text_chunks)
    return full_text[:40000] if len(full_text) > 40000 else full_text

@app.post("/api/generate-summary")
async def generate_summary(files: List[UploadFile] = File(...)):
    try:
        text = await extract_text_from_files(files)
        prompt = f"""
You are a construction estimating assistant.

From the documents below, extract:
1. "title": a short project name
2. "summary": a 4–6 sentence summary

Return JSON.

DOCUMENTS:
{text}
"""
        logger.info("Requesting GPT summary...")
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": "You are an expert construction estimator."},
                      {"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=3500
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```json"):
            raw = raw.strip("` \n").replace("json", "").strip()
        parsed = json.loads(raw.replace("'", '"'))
        return {"title": parsed.get("title", "Untitled Project"), "summary": parsed.get("summary", "")}
    except Exception as e:
        logger.error(f"Summary error: {str(e)}")
        return JSONResponse(status_code=500, content={"detail": f"Summary generation failed: {str(e)}"})

@app.post("/api/generate-divisions")
async def generate_divisions(files: List[UploadFile] = File(...)):
    try:
        text = await extract_text_from_files(files)
        prompt = f"""
From the construction documents below, return JSON with one key per CSI Division (01–33). Each key should summarize scope found under that division. If a division is not found, say "Division XX not found in documents."

DOCUMENTS:
{text}
"""
        logger.info("Requesting GPT division descriptions...")
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": "You are an expert construction estimator."},
                      {"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=3500
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```json"):
            raw = raw.strip("` \n").replace("json", "").strip()
        parsed = json.loads(raw.replace("'", '"'))
        return {"divisionDescriptions": parsed}
    except Exception as e:
        logger.error(f"Division error: {str(e)}")
        return JSONResponse(status_code=500, content={"detail": f"Division generation failed: {str(e)}"})

@app.post("/api/extract-takeoff")
async def extract_takeoff(files: List[UploadFile] = File(...)):
    try:
        text = await extract_text_from_files(files)
        all_takeoff = []

        for div_id, div_name in CSI_DIVISIONS.items():
            prompt = f"""
Extract takeoff items for Division {div_id} – {div_name}.

Return an array of items with:
- division
- description
- quantity
- unit
- unitCost
- modifier (optional)

DOCUMENTS:
{text}
"""
            try:
                logger.info(f"Extracting takeoff for Division {div_id}")
                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "system", "content": "You are an expert construction estimator."},
                              {"role": "user", "content": prompt}],
                    temperature=0.4,
                    max_tokens=3500
                )
                raw = response.choices[0].message.content.strip()
                if raw.startswith("```json"):
                    raw = raw.strip("` \n").replace("json", "").strip()
                items = json.loads(raw.replace("'", '"'))
                if isinstance(items, list):
                    all_takeoff.extend(items)
            except Exception as e:
                logger.warning(f"Skipped Division {div_id}: {str(e)}")
                continue

        return {"takeoff": all_takeoff}
    except Exception as e:
        logger.error(f"Takeoff error: {str(e)}")
        return JSONResponse(status_code=500, content={"detail": f"Takeoff generation failed: {str(e)}"})

@app.post("/api/chat")
async def chat(request: Request):
    try:
        data = await request.json()
        messages = data.get("discussion", [])
        project_data = data.get("project_data", {})

        prompt = f"Project data: {json.dumps(project_data, indent=2)}"
        logger.info("Initiating chat reply...")
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a helpful assistant familiar with construction estimating."},
                *[{"role": "user", "content": m["text"]} for m in messages if m["sender"] == "User"],
                {"role": "user", "content": prompt}
            ],
            temperature=0.5,
            max_tokens=1000
        )
        reply = response.choices[0].message.content.strip()
        return {"reply": reply}
    except Exception as e:
        logger.error(f"Chat error: {str(e)}")
        return JSONResponse(status_code=500, content={"detail": f"Chat failed: {str(e)}"})

@app.post("/api/submit-feedback")
async def submit_feedback(request: Request):
    try:
        data = await request.json()
        logger.info(f"Feedback received: {json.dumps(data)}")
        return {"message": "Feedback logged"}
    except Exception as e:
        logger.error(f"Feedback logging failed: {str(e)}")
        return JSONResponse(status_code=500, content={"detail": f"Feedback submission failed: {str(e)}"})
