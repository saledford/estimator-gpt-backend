from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
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
        # Combine file content
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

        # Extract title and summary
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
            if not title_summary["summary"]:
                logger.warning("GPT returned empty summary")
                raise ValueError("Empty summary returned")
        except Exception as e:
            logger.error(f"Failed to extract title/summary: {str(e)}")
            raise HTTPException(status_code=400, detail=f"Failed to generate valid project summary: {str(e)}")

        # Extract division descriptions
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

        # Extract takeoff items division by division
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

        # Validate takeoff results
        if not all_takeoff_items:
            logger.warning("GPT returned empty takeoff list")
            return JSONResponse(
                status_code=400,
                content={"detail": "GPT scan returned no usable summary or takeoff items."}
            )

        # Build final JSON response
        result = {
            "title": title_summary.get("title", "Untitled Project"),
            "summary": title_summary.get("summary", "Unable to generate project summary."),
            "divisionDescriptions": division_descriptions,
            "takeoff": all_takeoff_items
        }
        logger.info(f"Full scan completed: title={result['title']}, {len(result['takeoff'])} takeoff items")
        return JSONResponse(content=result)

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Full scan failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to scan project: {str(e)}")

@app.post("/api/chat")
async def chat(request: Request):
    try:
        data = await request.json()
        discussion = data.get("discussion", [])
        project_data = data.get("project_data", {})

        # Get project fields
        summary = project_data.get("summary", "")
        notes = json.dumps(project_data.get("notes", []), indent=2)
        divisionDescriptions = json.dumps(project_data.get("divisionDescriptions", {}), indent=2)
        takeoff = json.dumps(project_data.get("takeoff", []), indent=2)
        preferences = json.dumps(project_data.get("preferences", {}), indent=2)
        specIndex = project_data.get("specIndex", [])

        # Extract relevant spec sections using keyword match
        latest_question = discussion[-1]["text"].lower() if discussion else ""
        relevant_specs = []
        for s in specIndex:
            match_score = sum(1 for word in latest_question.split() if word in s["text"].lower())
            if match_score > 0:
                relevant_specs.append((match_score, s))
        relevant_specs.sort(reverse=True)
        top_matches = [s["title"] + "\n\n" + s["text"][:3000] for _, s in relevant_specs[:3]]
        spec_excerpt = "\n\n---\n\n".join(top_matches)

        # Build system prompt
        context = f"""
You are a construction estimator assistant helping a contractor.

PROJECT SUMMARY:
{summary}

NOTES:
{notes}

DIVISION SCOPES:
{divisionDescriptions}

TAKEOFF:
{takeoff}

PREFERENCES:
{preferences}

RELEVANT SPECS (from uploaded spec manual):
{spec_excerpt if spec_excerpt else 'None matched. Use best judgment.'}
"""

        messages = [
            {"role": "system", "content": context},
            *[{"role": "user", "content": m["text"]} for m in discussion if m["sender"] == "User"]
        ]

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=0.5,
            max_tokens=1200
        )

        raw = response.choices[0].message.content.strip()

        # Check for JSON-formatted action block (optional)
        reply = raw
        actions = []
        try:
            if "```json" in raw:
                block = raw.split("```json")[1].split("```")[0].strip()
                parsed = json.loads(block)
                reply = parsed.get("reply", reply)
                actions = parsed.get("actions", [])
        except Exception as parse_err:
            logger.warning(f"Failed to parse GPT actions: {str(parse_err)}")

        return {"reply": reply, "actions": actions}

    except Exception as e:
        logger.error(f"Chat error: {str(e)}")
        return JSONResponse(status_code=500, content={"detail": f"Chat failed: {str(e)}"})