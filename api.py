from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from typing import List
import fitz  # PyMuPDF
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

logging.basicConfig(
    filename="quote_parsing.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

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

UPLOAD_DIR = "./temp_uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.get("/")
async def root():
    logger.info("Root endpoint accessed")
    return {"message": "Estimator GPT backend is running"}

@app.post("/api/upload-file")
async def upload_file(file: UploadFile = File(...)):
    try:
        file_id = str(uuid.uuid4())
        path = os.path.join(UPLOAD_DIR, file_id)
        with open(path, "wb") as f:
            f.write(await file.read())
        logger.info(f"Uploaded file: {file.filename}, File ID: {file_id}")
        return {"fileId": file_id, "name": file.filename}
    except Exception as e:
        logger.error(f"Upload failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

@app.get("/api/get-file/{file_id}")
async def get_file(file_id: str):
    path = os.path.join(UPLOAD_DIR, file_id)
    if not os.path.exists(path):
        logger.error(f"File not found: {file_id}")
        raise HTTPException(status_code=404, detail="File not found")
    logger.info(f"Retrieved file: {file_id}")
    return FileResponse(path, filename=file_id)

@app.delete("/api/delete-file/{file_id}")
async def delete_file(file_id: str):
    path = os.path.join(UPLOAD_DIR, file_id)
    if os.path.exists(path):
        os.remove(path)
        logger.info(f"Deleted file: {file_id}")
        return {"message": f"{file_id} deleted"}
    logger.error(f"File not found for deletion: {file_id}")
    raise HTTPException(status_code=404, detail="File not found")

@app.post("/api/full-scan")
async def full_scan(files: List[UploadFile] = File(...)):
    if not files:
        logger.error("No files provided for full scan")
        raise HTTPException(status_code=400, detail="No files provided")

    try:
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

        # Title and Summary
        prompt_title_summary = f"""
You are a professional construction estimator AI.

From the following construction documents, extract the following:
1. "title": A short, clean project name
2. "summary": A 4–6 sentence narrative describing the full project scope

Return JSON:
{{ "title": "...", "summary": "..." }}

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
            raw = response.choices[0].message.content.strip()
            logger.info(f"Raw title/summary: {raw}")
            title_summary = json.loads(raw.replace("'", '"'))
            if not title_summary.get("summary"):
                raise ValueError("Empty summary")
        except Exception as e:
            logger.error(f"Title/summary failed: {str(e)}")
            raise HTTPException(status_code=400, detail=f"Failed to generate valid project summary: {str(e)}")

        # Division Descriptions
        prompt_divisions = f"""
From the construction documents, return descriptions for each CSI division 01–33. If missing, say 'Division XX not found in documents.'

Return JSON:
{{ "03": "...", ..., "33": "Division 33 not found in documents." }}

DOCUMENTS:
{document_text}
"""
        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are an expert construction estimator."},
                    {"role": "user", "content": prompt_divisions}
                ],
                temperature=0.4,
                max_tokens=3500
            )
            raw = response.choices[0].message.content.strip()
            logger.info(f"Raw division descriptions: {raw}")
            division_descriptions = json.loads(raw.replace("'", '"'))
        except Exception as e:
            logger.error(f"Division descriptions failed: {str(e)}")
            division_descriptions = {k: f"Division {k} not found in documents." for k in CSI_DIVISIONS}

        # Takeoff Items by Division
        all_takeoff = []
        for div_id, div_name in CSI_DIVISIONS.items():
            prompt = f"""
Extract takeoff items for Division {div_id} – {div_name} from the documents.

Return JSON array with:
- division
- description
- quantity
- unit
- unitCost
- modifier (optional)

DOCUMENTS:
{document_text}
"""
            try:
                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": "You are an expert construction estimator."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.4,
                    max_tokens=3500
                )
                raw = response.choices[0].message.content.strip()
                logger.info(f"Takeoff for division {div_id}: {raw}")
                items = json.loads(raw.replace("'", '"'))
                if isinstance(items, list):
                    all_takeoff.extend(items)
            except Exception as e:
                logger.warning(f"Takeoff failed for {div_id}: {str(e)}")
                continue

        if not title_summary.get("summary") or not all_takeoff:
            logger.warning("GPT scan returned no usable summary or takeoff")
            return JSONResponse(
                status_code=400,
                content={
                    "detail": "GPT scan returned no usable summary or takeoff items.",
                    "summary": title_summary.get("summary", ""),
                    "takeoff_count": len(all_takeoff)
                }
            )

        return JSONResponse(content={
            "title": title_summary.get("title", "Untitled Project"),
            "summary": title_summary["summary"],
            "divisionDescriptions": division_descriptions,
            "takeoff": all_takeoff
        })

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unhandled error in full scan: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Full scan failed: {str(e)}")
