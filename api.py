from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from typing import List, Dict
from pydantic import BaseModel
import fitz  # PyMuPDF
import re
import os
import uuid
import logging
import traceback
from dotenv import load_dotenv
from openai import OpenAI
import json

load_dotenv()
client = OpenAI()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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
files_storage: Dict[str, str] = {}  # Track file_id to filepath

@app.on_event("startup")
async def log_routes():
    for route in app.routes:
        logger.info(f"Route: {route.path} [{','.join(route.methods)}]")

@app.get("/")
async def root():
    logger.info("Root endpoint accessed")
    return {"message": "Estimator GPT backend is running"}

@app.post("/api/upload-file")
async def upload_file(file: UploadFile = File(...)):
    try:
        logger.info("📥 Received upload request.")
        logger.info(f"Filename: {file.filename}")

        if not file.filename:
            logger.error("❌ No file provided.")
            raise HTTPException(status_code=400, detail="No file uploaded.")

        file_id = str(uuid.uuid4())
        path = os.path.join("temp_uploads", file_id)

        with open(path, "wb") as f:
            f.write(await file.read())

        logger.info(f"✅ Saved: {file.filename} → {file_id}")
        return {"fileId": file_id, "name": file.filename}
    except Exception as e:
        logger.error(f"Upload failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

@app.get("/api/get-file/{file_id}")
async def get_file(file_id: str):
    if file_id not in files_storage:
        logger.error(f"File not found: {file_id}")
        raise HTTPException(status_code=404, detail="File not found")
    path = files_storage[file_id]
    if not os.path.exists(path):
        logger.error(f"File path does not exist: {path}")
        raise HTTPException(status_code=404, detail="File not found")
    try:
        logger.info(f"Retrieved file: {file_id}")
        return FileResponse(path, filename=file_id)
    except Exception as e:
        logger.error(f"Error retrieving file {file_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"File retrieval failed: {str(e)}")

@app.delete("/api/delete-file/{file_id}")
async def delete_file(file_id: str):
    if file_id not in files_storage:
        logger.error(f"File not found for deletion: {file_id}")
        raise HTTPException(status_code=404, detail="File not found")
    path = files_storage[file_id]
    if not os.path.exists(path):
        logger.error(f"File path does not exist for deletion: {path}")
        raise HTTPException(status_code=404, detail="File not found")
    try:
        os.remove(path)
        del files_storage[file_id]
        logger.info(f"Deleted file: {file_id}")
        return {"message": f"{file_id} deleted"}
    except Exception as e:
        logger.error(f"Error deleting file {file_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"File deletion failed: {str(e)}")

@app.post("/api/parse-spec")
async def parse_spec(file: UploadFile = File(...)):
    try:
        if not file.filename.lower().endswith(".pdf"):
            logger.error(f"Invalid file type: {file.filename}. Only PDF files are supported.")
            return JSONResponse(status_code=400, content={"detail": "Only PDF files are supported."})

        logger.info(f"Received file: {file.filename}")
        content = await file.read()
        doc = fitz.open(stream=content, filetype="pdf")

        sections = []
        section_text = ""
        current_section = None
        current_title = None

        for page in doc:
            text = page.get_text()
            lines = text.splitlines()
            for line in lines:
                if line.strip().startswith("SECTION"):
                    if current_section and section_text:
                        sections.append({
                            "section": current_section,
                            "title": current_title,
                            "text": section_text.strip()
                        })
                        section_text = ""
                    parts = line.strip().split(" ", 2)
                    if len(parts) >= 3:
                        current_section = parts[1].strip()
                        current_title = parts[2].strip()
                elif current_section:
                    section_text += line.strip() + "\n"

        if current_section and section_text:
            sections.append({
                "section": current_section,
                "title": current_title,
                "text": section_text.strip()
            })

        doc.close()
        logger.info(f"Parsed {len(sections)} spec sections from {file.filename}")
        return {"specIndex": sections}

    except Exception as e:
        import traceback
        logger.error(f"Spec parsing failed: {str(e)}")
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"detail": f"Spec parsing failed: {str(e)}"})

@app.post("/api/generate-summary")
async def generate_summary(files: List[UploadFile] = File(...)):
    if not files:
        logger.error("No files provided for summary generation")
        raise HTTPException(status_code=400, detail="No files provided")

    try:
        file_ids = []
        for file in files:
            if not file.filename:
                logger.error("Empty filename in summary file upload")
                raise HTTPException(status_code=400, detail="Empty filename provided")
            logger.info(f"Received file: {file.filename}")
            file_id = str(uuid.uuid4())
            path = os.path.join(UPLOAD_DIR, file_id)
            with open(path, "wb") as f:
                content = await file.read()
                f.write(content)
            files_storage[file_id] = path
            file_ids.append(file_id)
            logger.info(f"Stored file for summary: {file.filename}, File ID: {file_id}")

        text_parts = []
        for file_id in file_ids:
            if not os.path.exists(files_storage[file_id]):
                logger.error(f"File not found for summary: {file_id}")
                raise HTTPException(status_code=404, detail=f"File {file_id} not found")
            with open(files_storage[file_id], "rb") as f:
                if files_storage[file_id].lower().endswith(".pdf"):
                    doc = fitz.open(stream=f.read(), filetype="pdf")
                    for page in doc:
                        text_parts.append(page.get_text())
                    doc.close()
                else:
                    text_parts.append(f.read().decode("utf-8", errors="ignore"))

        document_text = "\n\n".join(text_parts)
        if len(document_text) > 40000:
            document_text = document_text[:40000]
            logger.warning("Truncated document text to 40,000 characters for summary")

        prompt = f"""
You are a construction estimator AI.

Generate:
1. "title": A short, clean project name (4–8 words, no symbols)
2. "summary": A detailed narrative (4–6 sentences) describing the project scope, building type, and key improvements.

Return JSON:
{{
  "title": "...",
  "summary": "..."
}}

DOCUMENTS:
{document_text}
"""
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are an expert construction estimator."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.4,
            max_tokens=3500
        )
        raw_response = response.choices[0].message.content.strip()
        logger.info(f"Raw GPT summary response: {raw_response}")
        parsed = json.loads(raw_response.replace("'", '"'))
        if not isinstance(parsed, dict) or "title" not in parsed or "summary" not in parsed:
            raise ValueError("Invalid summary response structure")

        safe_title = re.sub(r'[^a-zA-Z0-9 _-]', '', parsed["title"])
        return JSONResponse(content={"title": safe_title, "summary": parsed["summary"]})
    except Exception as e:
        logger.error(f"Summary generation failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Summary generation failed: {str(e)}")

@app.post("/api/generate-divisions")
async def generate_divisions(files: List[UploadFile] = File(...)):
    if not files:
        logger.error("No files provided for divisions generation")
        raise HTTPException(status_code=400, detail="No files provided")

    try:
        file_ids = []
        for file in files:
            if not file.filename:
                logger.error("Empty filename in divisions file upload")
                raise HTTPException(status_code=400, detail="Empty filename provided")
            logger.info(f"Received file: {file.filename}")
            file_id = str(uuid.uuid4())
            path = os.path.join(UPLOAD_DIR, file_id)
            with open(path, "wb") as f:
                content = await file.read()
                f.write(content)
            files_storage[file_id] = path
            file_ids.append(file_id)
            logger.info(f"Stored file for divisions: {file.filename}, File ID: {file_id}")

        text_parts = []
        for file_id in file_ids:
            if not os.path.exists(files_storage[file_id]):
                logger.error(f"File not found for divisions: {file_id}")
                raise HTTPException(status_code=404, detail=f"File {file_id} not found")
            with open(files_storage[file_id], "rb") as f:
                if files_storage[file_id].lower().endswith(".pdf"):
                    doc = fitz.open(stream=f.read(), filetype="pdf")
                    for page in doc:
                        text_parts.append(page.get_text())
                    doc.close()
                else:
                    text_parts.append(f.read().decode("utf-8", errors="ignore"))

        document_text = "\n\n".join(text_parts)
        if len(document_text) > 40000:
            document_text = document_text[:40000]
            logger.warning("Truncated document text to 40,000 characters for divisions")

        prompt = f"""
You are a construction estimator AI.

Identify all relevant CSI divisions in the documents. For each, provide a brief summary (1–2 sentences) indicating whether scope is detected.

Return JSON:
[
  {{
    "id": "03",
    "title": "Concrete",
    "summary": "Concrete scope detected including slabs and footings."
  }},
  ...
]

DOCUMENTS:
{document_text}
"""
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are an expert construction estimator."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.4,
            max_tokens=3500
        )
        raw_response = response.choices[0].message.content.strip()
        logger.info(f"Raw GPT divisions response: {raw_response}")
        parsed = json.loads(raw_response.replace("'", '"'))
        if not isinstance(parsed, list):
            raise ValueError("Invalid divisions response structure")

        return JSONResponse(content={"divisions": parsed})
    except Exception as e:
        logger.error(f"Divisions generation failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Divisions generation failed: {str(e)}")

@app.post("/api/extract-takeoff")
async def extract_takeoff(files: List[UploadFile] = File(...)):
    if not files:
        logger.error("No files provided for takeoff extraction")
        raise HTTPException(status_code=400, detail="No files provided")

    try:
        file_ids = []
        for file in files:
            if not file.filename:
                logger.error("Empty filename in takeoff file upload")
                raise HTTPException(status_code=400, detail="Empty filename provided")
            logger.info(f"Received file: {file.filename}")
            file_id = str(uuid.uuid4())
            path = os.path.join(UPLOAD_DIR, file_id)
            with open(path, "wb") as f:
                content = await file.read()
                f.write(content)
            files_storage[file_id] = path
            file_ids.append(file_id)
            logger.info(f"Stored file for takeoff: {file.filename}, File ID: {file_id}")

        text_parts = []
        for file_id in file_ids:
            if not os.path.exists(files_storage[file_id]):
                logger.error(f"File not found for takeoff: {file_id}")
                raise HTTPException(status_code=404, detail=f"File {file_id} not found")
            with open(files_storage[file_id], "rb") as f:
                if files_storage[file_id].lower().endswith(".pdf"):
                    doc = fitz.open(stream=f.read(), filetype="pdf")
                    for page in doc:
                        text_parts.append(page.get_text())
                    doc.close()
                else:
                    text_parts.append(f.read().decode("utf-8", errors="ignore"))

        document_text = "\n\n".join(text_parts)
        if len(document_text) > 40000:
            document_text = document_text[:40000]
            logger.warning("Truncated document text to 40,000 characters for takeoff")

        prompt = f"""
You are a construction estimator extracting takeoff items.

Return a JSON list of items with:
- division
- description
- quantity (numeric)
- unit (e.g., SF, LF, EA)
- unitCost (numeric, dollars)
- modifier (percent, optional)

Example:
[
  {{
    "division": "03",
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
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are an expert construction estimator."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.4,
            max_tokens=3500
        )
        raw_response = response.choices[0].message.content.strip()
        logger.info(f"Raw GPT takeoff response: {raw_response}")
        parsed = json.loads(raw_response.replace("'", '"'))
        if not isinstance(parsed, list):
            raise ValueError("Invalid takeoff response structure")

        if not parsed:
            logger.warning("GPT returned empty takeoff list")
            return JSONResponse(
                status_code=400,
                content={"detail": "GPT scan returned no usable takeoff items."}
            )

        return JSONResponse(content={"takeoff": parsed})
    except Exception as e:
        logger.error(f"Takeoff extraction failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Takeoff extraction failed: {str(e)}")

@app.post("/api/full-scan")
async def full_scan(files: List[UploadFile] = File(...)):
    if not files:
        logger.error("No files provided for full scan")
        raise HTTPException(status_code=400, detail="No files provided")

    try:
        file_ids = []
        for file in files:
            if not file.filename:
                logger.error("Empty filename in full scan file upload")
                raise HTTPException(status_code=400, detail="Empty filename provided")
            logger.info(f"Received file: {file.filename}")
            file_id = str(uuid.uuid4())
            path = os.path.join(UPLOAD_DIR, file_id)
            with open(path, "wb") as f:
                content = await file.read()
                f.write(content)
            files_storage[file_id] = path
            file_ids.append(file_id)
            logger.info(f"Stored file for full scan: {file.filename}, File ID: {file_id}")

        text_parts = []
        for file_id in file_ids:
            if not os.path.exists(files_storage[file_id]):
                logger.error(f"File not found for full scan: {file_id}")
                raise HTTPException(status_code=404, detail=f"File {file_id} not found")
            with open(files_storage[file_id], "rb") as f:
                if files_storage[file_id].lower().endswith(".pdf"):
                    doc = fitz.open(stream=f.read(), filetype="pdf")
                    for page in doc:
                        text_parts.append(page.get_text())
                    doc.close()
                elif files_storage[file_id].lower().endswith(".xlsx"):
                    text_parts.append(f"(Excel file uploaded: {os.path.basename(files_storage[file_id])})")
                elif files_storage[file_id].lower().endswith(".docx"):
                    text_parts.append(f"(Word doc uploaded: {os.path.basename(files_storage[file_id])})")
                else:
                    text_parts.append(f.read().decode("utf-8", errors="ignore"))

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
        relevant_specs.sort(key=lambda x: x[0], reverse=True)
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