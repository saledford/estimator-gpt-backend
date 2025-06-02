from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import List, Dict
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

# Configure logging
logging.basicConfig(
    filename="quote_parsing.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Constants
CSI_DIVISIONS = {
    "01": "General Requirements",
    "02": "Existing Conditions",
    "03": "Concrete",
    "04": "Masonry",
    "05": "Metals",
    "06": "Wood, Plastics, and Composites",
    "07": "Thermal and Moisture Protection",
    "08": "Openings (Doors, Windows)",
    "09": "Finishes",
    "10": "Specialties",
    "11": "Equipment",
    "12": "Furnishings",
    "13": "Special Construction",
    "14": "Conveying Equipment (Elevators)",
    "21": "Fire Suppression",
    "22": "Plumbing",
    "23": "HVAC",
    "25": "Integrated Automation",
    "26": "Electrical",
    "27": "Communications",
    "28": "Electronic Safety and Security",
    "31": "Earthwork",
    "32": "Exterior Improvements",
    "33": "Utilities"
}

UPLOAD_FOLDER = "temp_uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
files_storage: Dict[str, str] = {}  # {file_id: file_path}

@app.get("/")
async def root():
    logger.info("Root endpoint accessed")
    return {"message": "Estimator GPT backend is running"}

@app.post("/api/upload-file")
async def upload_file(file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".pdf", ".xlsx")):
        logger.error(f"Invalid file type for {file.filename}: Only PDF and Excel files are allowed")
        raise HTTPException(status_code=400, detail="Only PDF and Excel files are allowed")

    file_id = str(uuid.uuid4())
    filename = f"{file_id}_{file.filename}"
    filepath = os.path.join(UPLOAD_FOLDER, filename)

    try:
        with open(filepath, "wb") as f:
            content = await file.read()
            f.write(content)
        files_storage[file_id] = filepath
        logger.info(f"Uploaded file: {filename}, File ID: {file_id}")
        return {"fileId": file_id}
    except Exception as e:
        logger.error(f"Failed to save file {file.filename}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")

@app.get("/api/get-file/{file_id}")
async def get_file(file_id: str):
    if file_id not in files_storage:
        logger.error(f"File not found: {file_id}")
        raise HTTPException(status_code=404, detail="File not found")
    filepath = files_storage[file_id]
    if not os.path.exists(filepath):
        logger.error(f"File path does not exist: {filepath}")
        raise HTTPException(status_code=404, detail="File not found")
    try:
        filename = os.path.basename(filepath)
        media_type = "application/pdf" if filename.endswith(".pdf") else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        logger.info(f"Retrieved file: {filename}, File ID: {file_id}")
        return FileResponse(filepath, media_type=media_type, filename=filename)
    except Exception as e:
        logger.error(f"Error retrieving file {file_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/delete-file/{file_id}")
async def delete_file(file_id: str):
    if file_id not in files_storage:
        logger.error(f"File not found for deletion: {file_id}")
        raise HTTPException(status_code=404, detail="File not found")
    filepath = files_storage[file_id]
    if not os.path.exists(filepath):
        logger.error(f"File path does not exist for deletion: {filepath}")
        raise HTTPException(status_code=404, detail="File not found")
    try:
        os.remove(filepath)
        del files_storage[file_id]
        logger.info(f"Deleted file: {filepath}")
        return {"message": "File deleted successfully"}
    except Exception as e:
        logger.error(f"Error deleting file {file_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/full-scan")
async def full_scan(files: List[UploadFile] = File(...)):
    if not files:
        logger.error("No files provided for full scan")
        raise HTTPException(status_code=400, detail="No files provided")

    try:
        # Stage 1: Combine uploaded documents
        text_parts = []
        for file in files:
            content = await file.read()
            if file.filename.lower().endswith(".pdf"):
                doc = fitz.open(stream=content, filetype="pdf")
                for page in doc:
                    text_parts.append(page.get_text())
                doc.close()
            elif file.filename.lower().endswith(".xlsx"):
                text_parts.append(f"(Excel file uploaded: {file.filename})")
            elif file.filename.lower().endswith(".docx"):
                text_parts.append(f"(Word doc uploaded: {file.filename})")
            else:
                text_parts.append(content.decode("utf-8", errors="ignore"))
            logger.info(f"Processed file: {file.filename}")

        document_text = "\n\n".join(text_parts)
        if len(document_text) > 40000:
            document_text = document_text[:40000]
            logger.warning("Truncated document text to 40,000 characters")

        # Stage 2: Extract title and summary
        prompt_title_summary = f"""
You are a professional construction estimator AI.

From the following construction documents, extract the following:
1. "title": A short, clean project name (no file names or markup)
2. "summary": A high-level narrative description of the overall project. It should give the estimator a full understanding of the building type, renovations/improvements, and what work is likely to be involved. Minimum 4–6 sentences.

Return JSON:
{{
  "title": "...",
  "summary": "..."
}}

DOCUMENTS:
{document_text}
"""
        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are an expert construction estimator."},
                    {"role": "user", "content": prompt_title_summary}
                ],
                temperature=0.4,
                max_tokens=3500
            )
            raw_title_summary = response.choices[0].message.content.strip()
            logger.info(f"Raw GPT title/summary response: {raw_title_summary}")
            title_summary = json.loads(raw_title_summary.replace("'", '"'))
            if not isinstance(title_summary, dict) or "title" not in title_summary or "summary" not in title_summary:
                raise ValueError("Invalid title/summary structure")
        except Exception as e:
            logger.error(f"Failed to extract title/summary: {str(e)}")
            title_summary = {"title": "Untitled Project", "summary": "Unable to generate project summary."}

        # Stage 3: Extract division descriptions
        prompt_division_descriptions = f"""
From the following construction documents, return a dictionary of CSI division scope descriptions.

For each of Divisions 01 through 33:
- If work is found for the division, describe it in 2–4 detailed sentences.
- If not found, mark it as: "Division [XX] not found in documents."

Return JSON:
{{
  "01": "...",
  "02": "...",
  ...
  "33": "Division 33 not found in documents."
}}

DOCUMENTS:
{document_text}
"""
        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are an expert construction estimator."},
                    {"role": "user", "content": prompt_division_descriptions}
                ],
                temperature=0.4,
                max_tokens=3500
            )
            raw_divisions = response.choices[0].message.content.strip()
            logger.info(f"Raw GPT divisions response: {raw_divisions}")
            division_descriptions = json.loads(raw_divisions.replace("'", '"'))
            if not isinstance(division_descriptions, dict):
                raise ValueError("Invalid division descriptions structure")
        except Exception as e:
            logger.error(f"Failed to extract division descriptions: {str(e)}")
            division_descriptions = {div: f"Division {div} not found in documents." for div in CSI_DIVISIONS}

        # Stage 4: Extract takeoff items division by division
        all_takeoff_items = []
        for division_id, division_title in CSI_DIVISIONS.items():
            prompt_takeoff = f"""
Extract a list of itemized takeoff entries **only** for Division {division_id} – {division_title} from the documents below.

Format as JSON array. Each item must include:
- division
- description
- quantity (numeric)
- unit (e.g. SF, LF, EA)
- unitCost (numeric, dollars)
- modifier (optional, percent)

Example:
[
  {{
    "division": "{division_id}",
    "description": "Pour 6-inch slab on grade",
    "quantity": 2040,
    "unit": "SF",
    "unitCost": 9.90,
    "modifier": 0
  }}
]

DOCUMENTS:
{document_text}
"""
            try:
                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": "You are an expert construction estimator."},
                        {"role": "user", "content": prompt_takeoff}
                    ],
                    temperature=0.4,
                    max_tokens=3500
                )
                raw_takeoff = response.choices[0].message.content.strip()
                logger.info(f"Raw GPT takeoff response for division {division_id}: {raw_takeoff}")
                takeoff_items = json.loads(raw_takeoff.replace("'", '"'))
                if isinstance(takeoff_items, list) and takeoff_items:
                    all_takeoff_items.extend(takeoff_items)
            except Exception as e:
                logger.error(f"Failed to extract takeoff for division {division_id}: {str(e)}")
                continue

        # Stage 5: Build final JSON response
        result = {
            "title": title_summary.get("title", "Untitled Project"),
            "summary": title_summary.get("summary", "Unable to generate project summary."),
            "divisionDescriptions": division_descriptions,
            "takeoff": all_takeoff_items
        }
        logger.info(f"Full scan completed: title={result['title']}, {len(result['takeoff'])} takeoff items")
        return JSONResponse(content=result)

    except Exception as e:
        logger.error(f"Full scan failed: {str(e)}")
        return JSONResponse(status_code=500, content={"detail": f"Failed to scan project: {str(e)}"})