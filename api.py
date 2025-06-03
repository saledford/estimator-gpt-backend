from fastapi import FastAPI, UploadFile, File
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
        logger.error(f"File not found: {file_id}")
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

@app.post("/api/full-scan")
async def full_scan(files: List[UploadFile] = File(...)):
    if not files:
        return JSONResponse(status_code=400, content={"detail": "No files provided"})

    try:
        # Step 1: Combine all file text (PDFs, etc.)
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
        if len(full_text) > 40000:
            full_text = full_text[:40000]
            logger.warning("Text truncated to 40,000 characters")

        # Step 2: Title & Summary
        logger.info("Requesting GPT title/summary...")
        prompt_summary = f"""
You are a construction estimating assistant.

From the documents below, extract:
1. "title": a short project name
2. "summary": a 4–6 sentence summary

Return JSON.

DOCUMENTS:
{full_text}
"""
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": "You are an expert construction estimator."},
                      {"role": "user", "content": prompt_summary}],
            temperature=0.4,
            max_tokens=3500
        )
        content = response.choices[0].message.content.strip()
        if content.startswith("```json"):
            content = content.strip("` \n").replace("json", "").strip()
        title_summary = json.loads(content.replace("'", '"'))
        title = title_summary.get("title", "Untitled Project")
        summary = title_summary.get("summary", "")
        logger.info(f"Extracted title: {title}")

        # Step 3: Division Descriptions
        logger.info("Requesting GPT division descriptions...")
        prompt_divs = f"""
From these construction documents, return a JSON object with keys from Division 01 to Division 33.

Each key should contain a short summary of scope under that division. If not found, say "Division XX not found in documents."

DOCUMENTS:
{full_text}
"""
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": "You are an expert construction estimator."},
                      {"role": "user", "content": prompt_divs}],
            temperature=0.4,
            max_tokens=3500
        )
        raw_divs = response.choices[0].message.content.strip()
        if raw_divs.startswith("```json"):
            raw_divs = raw_divs.strip("` \n").replace("json", "").strip()
        division_descriptions = json.loads(raw_divs.replace("'", '"'))
        logger.info("Division descriptions parsed")

        # Step 4: Takeoff Items Per Division
        all_takeoff = []
        for div_id, div_name in CSI_DIVISIONS.items():
            prompt_takeoff = f"""
Extract takeoff items for Division {div_id} – {div_name}.

Return an array of items with:
- division
- description
- quantity
- unit
- unitCost
- modifier (optional)

DOCUMENTS:
{full_text}
"""
            try:
                logger.info(f"Extracting takeoff for Division {div_id}")
                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "system", "content": "You are an expert construction estimator."},
                              {"role": "user", "content": prompt_takeoff}],
                    temperature=0.4,
                    max_tokens=3500
                )
                raw_takeoff = response.choices[0].message.content.strip()
                if raw_takeoff.startswith("```json"):
                    raw_takeoff = raw_takeoff.strip("` \n").replace("json", "").strip()
                items = json.loads(raw_takeoff.replace("'", '"'))
                if isinstance(items, list):
                    all_takeoff.extend(items)
            except Exception as e:
                logger.warning(f"Skipping takeoff for {div_id}: {str(e)}")
                continue

        if not summary or not all_takeoff:
            logger.warning("Missing key results from GPT scan.")
            return JSONResponse(
                status_code=400,
                content={"detail": "GPT scan returned no usable summary or takeoff items.",
                         "summary": summary,
                         "takeoff_count": len(all_takeoff)}
            )

        return JSONResponse(content={
            "title": title,
            "summary": summary,
            "divisionDescriptions": division_descriptions,
            "takeoff": all_takeoff
        })

    except Exception as e:
        logger.error(f"Unhandled error during scan: {str(e)}")
        return JSONResponse(status_code=500, content={"detail": f"Full scan failed: {str(e)}"})
