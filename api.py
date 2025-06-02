from fastapi import FastAPI, UploadFile, File, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from typing import List, Dict
from pydantic import BaseModel
import fitz  # PyMuPDF
import re
import os
import uuid
import logging
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI
import json
import shutil

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

DIVISION_KEYWORDS = {
    "03": ["concrete", "slab", "footing", "foundation"],
    "04": ["masonry", "brick", "block", "stone"],
    "05": ["steel", "metal", "structural steel"],
    "06": ["wood", "timber", "plastic", "composite"],
    "07": ["roofing", "insulation", "waterproofing"],
    "08": ["door", "window", "glazing", "frame"],
    "09": ["paint", "drywall", "gypsum", "finish", "flooring", "tile"],
    "10": ["signage", "lockers", "partitions"],
    "11": ["equipment", "appliances", "kitchen equipment"],
    "12": ["furniture", "seating", "blinds"],
    "13": ["prefabricated", "special construction"],
    "14": ["elevator", "escalator", "lift"],
    "21": ["fire sprinkler", "fire suppression"],
    "22": ["plumbing", "fixture", "pipe", "valve"],
    "23": ["hvac", "mechanical", "ventilation", "air handler"],
    "26": ["electrical", "receptacle", "lighting", "panel", "wiring"],
    "27": ["communications", "cabling", "network"],
    "28": ["security", "alarms", "cameras"],
    "31": ["earthwork", "excavation", "grading"],
    "32": ["paving", "landscaping", "fencing"],
    "33": ["utilities", "sewer", "water line", "storm drain"]
}

UPLOAD_FOLDER = "temp_uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

files_storage: Dict[str, str] = {}  # {file_id: file_path}
feedback_storage: List[Dict] = []  # In-memory feedback storage

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
        raise HTTPException(status_code=500, detail=f"Failed to save file {file.filename}: {str(e)}")

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

@app.post("/api/extract-text")
async def extract_text(files: List[UploadFile] = File(...)):
    if not files:
        logger.error("No files provided for text extraction")
        raise HTTPException(status_code=400, detail="No files provided")

    full_text = ""
    for file in files:
        try:
            content = await file.read()
            doc = fitz.open(stream=content, filetype="pdf")
            for page in doc:
                full_text += page.get_text()
            doc.close()
            logger.info(f"Text extracted from file: {file.filename}")
        except Exception as e:
            logger.error(f"Error extracting text from {file.filename}: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Error processing {file.filename}: {str(e)}")

    return {"text": full_text}

@app.get("/api/extract-divisions")
async def get_divisions():
    logger.info("CSI divisions list requested")
    return {"divisions": [{"id": k, "title": v} for k, v in CSI_DIVISIONS.items()]}

@app.post("/api/extract-takeoff/{division_id}")
async def extract_takeoff(division_id: str, files: List[UploadFile] = File(...)):
    if not files:
        logger.error("No files provided for takeoff extraction")
        raise HTTPException(status_code=400, detail="No files provided")

    if division_id not in CSI_DIVISIONS:
        logger.error(f"Invalid division ID: {division_id}")
        raise HTTPException(status_code=400, detail="Invalid division ID")

    full_text = ""
    for file in files:
        try:
            content = await file.read()
            doc = fitz.open(stream=content, filetype="pdf")
            for page in doc:
                full_text += page.get_text()
            doc.close()
            logger.info(f"Text extracted for takeoff from file: {file.filename}")
        except Exception as e:
            logger.error(f"Error extracting text for takeoff from {file.filename}: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Error processing {file.filename}: {str(e)}")

    try:
        prompt = (
            f"You are a construction estimator analyzing takeoff data for Division {division_id} – {CSI_DIVISIONS[division_id]}.\n"
            f"Extract only takeoff items related to this division from the provided project text.\n"
            "Return a JSON list where each item has: division, description, quantity, unit, unitCost, and modifier.\n"
            f"Project Text:\n{full_text[:12000]}\n\nTakeoff Items (JSON list):"
        )
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a construction estimator parsing division-specific takeoff data."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1000,
            temperature=0.3
        )
        parsed = response.choices[0].message.content.strip()
        try:
            takeoff = json.loads(parsed.replace("'", '"'))
            if not isinstance(takeoff, list):
                raise ValueError("GPT response is not a list")
            for item in takeoff:
                item["division"] = division_id
            logger.info(f"Extracted {len(takeoff)} takeoff items for division {division_id}")
        except Exception as e:
            logger.error(f"Error parsing takeoff response for division {division_id}: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Error parsing takeoff response: {str(e)}")

        return {"takeoff": takeoff}
    except Exception as e:
        logger.error(f"Error generating takeoff for division {division_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error generating takeoff: {str(e)}")

@app.post("/api/full-scan")
async def full_scan(files: List[UploadFile] = File(...)):
    if not files:
        logger.error("No files provided for full scan")
        raise HTTPException(status_code=400, detail="No files provided")

    try:
        # Combine contents of all uploaded files
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

        combined_text = "\n\n".join(text_parts)
        if len(combined_text) > 30000:
            combined_text = combined_text[:30000]
            logger.warning("Truncated combined text to 30,000 characters")

        # Send to GPT-4o
        prompt = f"""
You are a professional construction estimator AI.

From the following documents, generate the following structured JSON:
1. "title": Short project name (e.g. "City of Wilson Fire Station Renovation")
2. "summary": Overview of the project scope and purpose
3. "divisionDescriptions": CSI division scope descriptions, mapped to keys like "03", "09"
4. "takeoff": List of items with fields:
   - division
   - description
   - quantity
   - unit
   - unitCost
   - modifier (optional)

ONLY return valid JSON:
{{
  "title": "...",
  "summary": "...",
  "divisionDescriptions": {{ "03": "...", "09": "..." }},
  "takeoff": [
    {{
      "division": "03",
      "description": "...",
      "quantity": 100,
      "unit": "SF",
      "unitCost": 12.5,
      "modifier": 0
    }}
  ]
}}

Documents:
{combined_text}
"""
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are an expert construction estimator."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.4,
            max_tokens=3000
        )

        reply = response.choices[0].message.content.strip()
        try:
            result = json.loads(reply.replace("'", '"'))
            if not isinstance(result, dict) or not all(key in result for key in ["title", "summary", "divisionDescriptions", "takeoff"]):
                raise ValueError("Invalid response structure")
            logger.info(f"Full scan completed: title={result['title']}, {len(result['takeoff'])} takeoff items")
            return JSONResponse(content=result)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse GPT response: {reply}")
            return JSONResponse(status_code=500, content={"detail": "Failed to parse GPT response"})
        except ValueError as e:
            logger.error(f"Invalid GPT response structure: {str(e)}")
            return JSONResponse(status_code=500, content={"detail": "Invalid GPT response structure"})

    except Exception as e:
        logger.error(f"Failed to scan project: {str(e)}")
        return JSONResponse(status_code=500, content={"detail": f"Failed to scan project: {str(e)}"})

class DivisionAnalysisRequest(BaseModel):
    quotes: List[Dict]
    takeoff: List[Dict]
    specs: str

@app.post("/api/analyze-division/{division_id}")
async def analyze_division(division_id: str, request: DivisionAnalysisRequest):
    if division_id not in CSI_DIVISIONS:
        logger.error(f"Invalid division ID: {division_id}")
        raise HTTPException(status_code=400, detail="Invalid division ID")

    try:
        quotes = request.quotes
        takeoff = request.takeoff
        specs = request.specs

        if not quotes:
            logger.error(f"No quotes provided for division {division_id}")
            raise HTTPException(status_code=400, detail="Quotes are required")

        prompt = (
            "You are a construction project analyst using GPT-4o to analyze a specific CSI division for a construction project. "
            "Review the provided quote, takeoff items, and specifications for the division. "
            "Identify any potential issues, such as unusually low costs, missing takeoff items, or discrepancies between specs and takeoff. "
            "Return a dictionary with a 'quote' field containing any warnings about the quote (e.g., 'Cost seems unusually low'), "
            "and a 'takeoff' field containing warnings about takeoff items (e.g., 'Quantity seems off'). "
            "If no issues are found, return empty strings for each field.\n\n"
            f"Division ID: {division_id} – {CSI_DIVISIONS[division_id]}\n"
            f"Quote: {json.dumps(quotes[0])}\n"
            f"Takeoff Items: {json.dumps(takeoff)}\n"
            f"Specifications: {specs[:12000]}\n\n"
            "Analysis (as a JSON dictionary):"
        )
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a construction project analyst with expertise in analyzing project data."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=300,
            temperature=0.5
        )
        analysis_raw = response.choices[0].message.content.strip()
        logger.warning(f"Raw GPT response for division {division_id}:\n{analysis_raw}")
        try:
            analysis = json.loads(analysis_raw)
        except json.JSONDecodeError:
            analysis_raw = analysis_raw.replace("'", '"').strip()
            if not analysis_raw or analysis_raw[0] not in ['{', '[']:
                logger.warning(f"GPT returned invalid or empty JSON response for division {division_id}")
                return {"warnings": {"quote": "", "takeoff": ""}}
            analysis = json.loads(analysis_raw)

        if not isinstance(analysis, dict) or "quote" not in analysis or "takeoff" not in analysis:
            raise ValueError("Response must be a dictionary with 'quote' and 'takeoff' fields")
        logger.info(f"Analysis completed for division {division_id}: {analysis}")
        return {"warnings": analysis}
    except Exception as e:
        logger.error(f"Error analyzing division {division_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error analyzing division {division_id}: {str(e)}")

class ChatRequest(BaseModel):
    discussion: List[Dict]
    project_data: Dict

@app.post("/api/chat")
async def chat(request: ChatRequest):
    try:
        discussion = request.discussion
        project_data = request.project_data

        discussion_text = "\n".join([f"{msg['sender']}: {msg['text']}" for msg in discussion])
        prompt = (
            "You are a construction project assistant using GPT-4o to assist with project analysis and discussion. "
            "The user has provided a conversation history and project data (quotes, takeoff, financials). "
            "Respond to the user's latest message in the discussion, providing insights or suggestions based on the project data. "
            "Keep the response concise and professional, focusing on actionable advice or clarifications.\n\n"
            f"Discussion History:\n{discussion_text}\n\n"
            f"Project Data:\n{json.dumps(project_data)}\n\n"
            "Your Response:"
        )
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a construction project assistant with expertise in project analysis."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=200,
            temperature=0.7
        )
        reply = response.choices[0].message.content.strip()
        logger.info(f"Chat response generated: {reply}")
        return {"reply": reply}
    except Exception as e:
        logger.error(f"Error in chat: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error in chat: {str(e)}")

@app.post("/api/submit-feedback")
async def submit_feedback(feedback: dict):
    try:
        item_id = feedback.get("itemId")
        change_type = feedback.get("type")
        old_value = feedback.get("oldValue")
        new_value = feedback.get("newValue")
        note = feedback.get("note", "")

        if not item_id or not change_type:
            logger.error("Missing required feedback fields: itemId or type")
            raise HTTPException(status_code=400, detail="Missing required fields: itemId or type")

        feedback_entry = {
            "itemId": item_id,
            "type": change_type,
            "oldValue": old_value,
            "newValue": new_value,
            "note": note,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        feedback_storage.append(feedback_entry)
        logger.info(f"Feedback received for item {item_id}: {change_type} changed from {old_value} to {new_value} – {note}")
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error submitting feedback: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error submitting feedback: {str(e)}")

@app.post("/api/classify-item")
async def classify_item(data: dict):
    try:
        description = data.get("description", "").strip()
        if not description:
            logger.error("Empty description provided for item classification")
            raise HTTPException(status_code=400, detail="Description is required")

        prompt = (
            "You are a construction project analyst tasked with classifying a takeoff item into a CSI division. "
            "Analyze the provided item description and return the most likely CSI division in the format 'Division XX – Title'. "
            "Respond only with the division number and name, e.g., 'Division 26 – Electrical'. "
            "If unsure, return an empty string.\n\n"
            f"Item Description: {description}\n\n"
            "Division:"
        )

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a construction project analyst with expertise in CSI division classification."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=50,
            temperature=0.5
        )

        division = response.choices[0].message.content.strip()
        logger.info(f"Classified item '{description}' as '{division}'")
        return {"division": division}
    except Exception as e:
        logger.error(f"Error classifying item: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"detail": f"Error classifying item: {str(e)}", "division": ""}
        )