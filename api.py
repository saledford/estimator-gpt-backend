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

@app.get("/")
def root():
    return {"message": "Estimator GPT backend is running"}

UPLOAD_FOLDER = "temp_uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# In-memory storage for files (replace with a database in production)
files_storage: Dict[str, str] = {}  # {file_id: file_path}

@app.post("/api/upload-file")
async def upload_file(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(('.pdf', '.xlsx')):
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
            logger.info(f"Extracted text from file: {file.filename}")
        except Exception as e:
            logger.error(f"Error extracting text from {file.filename}: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Error extracting text from {file.filename}: {str(e)}")

    return {"text": full_text}

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

DIVISION_KEYWORDS = {
    "03": ["concrete", "slab", "footing"],
    "08": ["door", "window", "glazing"],
    "09": ["paint", "drywall", "gypsum", "finish", "flooring"],
    "22": ["plumbing", "fixture", "pipe"],
    "23": ["hvac", "mechanical", "ventilation", "air handler"],
    "26": ["electrical", "receptacle", "lighting", "panel"]
}

@app.post("/api/parse-structured")
async def parse_structured(files: List[UploadFile] = File(...)):
    if not files:
        logger.error("No files provided for structured parsing")
        raise HTTPException(status_code=400, detail="No files provided")

    full_text = ""
    suggested_name = ""

    for file in files:
        try:
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
        except Exception as e:
            logger.error(f"Error parsing structured data from {file.filename}: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Error parsing {file.filename}: {str(e)}")

    quotes = []
    for div, name in CSI_DIVISIONS:
        summary = "Not detected"
        for keyword in DIVISION_KEYWORDS.get(div, []):
            if keyword in full_text:
                summary = f"{name} scope detected based on presence of '{keyword}'"
                break
        quotes.append({
            "id": div,
            "title": name,
            "summary": summary,
            "cost": 0,
            "markup": 10,
            "finalPrice": 0
        })

    logger.info(f"Structured parsing completed. Suggested project name: {suggested_name}, Quotes: {len(quotes)} divisions")
    return {
        "quotes": quotes,
        "suggestedProjectName": suggested_name
    }

@app.post("/api/generate-summary")
async def generate_summary(files: List[UploadFile] = File(...)):
    if not files:
        logger.error("No files provided for summary generation")
        raise HTTPException(status_code=400, detail="No files provided")

    full_text = ""
    for file in files:
        try:
            content = await file.read()
            doc = fitz.open(stream=content, filetype="pdf")
            for page in doc:
                full_text += page.get_text()
            doc.close()
            logger.info(f"Text extracted for summary from file: {file.filename}")
        except Exception as e:
            logger.error(f"Error extracting text for summary from {file.filename}: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Error processing {file.filename}: {str(e)}")

    # Truncate text to 12,000 characters and log token estimates
    estimated_tokens = len(full_text.split()) * 1.33
    logger.warning(f"Token estimate before truncation: {estimated_tokens:.0f}")
    full_text_truncated = full_text[:12000]
    estimated_tokens_after = len(full_text_truncated.split()) * 1.33
    logger.warning(f"Token estimate after truncation: {estimated_tokens_after:.0f}")

    try:
        prompt = (
            "You are an architect tasked with summarizing a construction project based on the provided specifications. "
            "Generate a concise, professional summary of the project in a narrative style, focusing on key aspects such as "
            "the type of project, major divisions involved (e.g., concrete, HVAC, electrical), and any notable features. "
            "The summary should be 2-3 sentences long.\n\n"
            f"Project Specifications:\n{full_text_truncated}\n\n"
            "Summary:"
        )
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a professional architect with expertise in summarizing construction projects."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=150,
            temperature=0.7
        )
        summary = response.choices[0].message.content.strip()
        if not summary:
            logger.error("Empty summary received from GPT")
            raise ValueError("Empty summary received from GPT")
        logger.info("Project summary generated successfully")
        return {"summary": summary}
    except Exception as e:
        logger.error(f"Error generating summary: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error generating summary: {str(e)}")

@app.post("/api/parse-specs")
async def parse_specs(files: List[UploadFile] = File(...)):
    if not files:
        logger.error("No files provided for specs parsing")
        raise HTTPException(status_code=400, detail="No files provided")

    full_text = ""
    for file in files:
        try:
            content = await file.read()
            doc = fitz.open(stream=content, filetype="pdf")
            for page in doc:
                full_text += page.get_text()
            doc.close()
            logger.info(f"Text extracted for specs parsing from file: {file.filename}")
        except Exception as e:
            logger.error(f"Error extracting text for specs from {file.filename}: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Error processing {file.filename}: {str(e)}")

    # Truncate text to 12,000 characters and log token estimates
    estimated_tokens = len(full_text.split()) * 1.33
    logger.warning(f"Token estimate before truncation: {estimated_tokens:.0f}")
    full_text_truncated = full_text[:12000]
    estimated_tokens_after = len(full_text_truncated.split()) * 1.33
    logger.warning(f"Token estimate after truncation: {estimated_tokens_after:.0f}")

    try:
        prompt = (
            "You are a construction project analyst tasked with extracting detailed specifications for CSI divisions from project documents. "
            "Analyze the provided text and identify specifications for each CSI division present. "
            "Return a dictionary where keys are division IDs (e.g., '03', '09') and values are detailed descriptions of the specifications for that division. "
            "If a division is not mentioned, exclude it from the dictionary. "
            "Focus on materials, methods, and notable features. "
            "Respond in strict JSON format without markdown or extra text. Example structure:\n"
            "{\"03\": \"Concrete specifications including...\", \"09\": \"Finishes specifications including...\"}\n\n"
            f"Project Text:\n{full_text_truncated}\n\n"
            "Specifications by Division:"
        )
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a construction project analyst with expertise in parsing specifications."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=500,
            temperature=0.5
        )
        descriptions_raw = response.choices[0].message.content.strip()
        if not descriptions_raw:
            logger.error("Empty response received from GPT for specs parsing")
            return JSONResponse(
                status_code=500,
                content={"detail": "Empty response received from GPT"}
            )
        logger.debug(f"Raw GPT response for specs: {descriptions_raw}")
        # Attempt to parse the response as JSON
        try:
            descriptions = json.loads(descriptions_raw)
            if not isinstance(descriptions, dict):
                raise ValueError("Response is not a dictionary")
        except json.JSONDecodeError:
            # Try to clean and parse
            descriptions_raw = descriptions_raw.replace("'", '"')
            try:
                descriptions = json.loads(descriptions_raw)
                if not isinstance(descriptions, dict):
                    raise ValueError("Response is not a dictionary")
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse GPT response as JSON: {descriptions_raw}")
                return JSONResponse(
                    status_code=500,
                    content={"detail": f"Failed to parse GPT response: {str(e)}"}
                )
        logger.info(f"Parsed specifications for {len(descriptions)} divisions")
        return {"descriptions": str(descriptions)}  # Return as string to match frontend expectation
    except Exception as e:
        logger.error(f"Error parsing specs: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"detail": f"Error parsing specs: {str(e)}"}
        )

@app.post("/api/parse-takeoff")
async def parse_takeoff(files: List[UploadFile] = File(...)):
    if not files:
        logger.error("No files provided for takeoff parsing")
        raise HTTPException(status_code=400, detail="No files provided")

    full_text = ""
    for file in files:
        try:
            content = await file.read()
            doc = fitz.open(stream=content, filetype="pdf")
            for page in doc:
                full_text += page.get_text()
            doc.close()
            logger.info(f"Text extracted for takeoff parsing from file: {file.filename}")
        except Exception as e:
            logger.error(f"Error extracting text for takeoff from {file.filename}: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Error processing {file.filename}: {str(e)}")

    # Truncate text to 12,000 characters and log token estimates
    estimated_tokens = len(full_text.split()) * 1.33
    logger.warning(f"Token estimate before truncation: {estimated_tokens:.0f}")
    full_text_truncated = full_text[:12000]
    estimated_tokens_after = len(full_text_truncated.split()) * 1.33
    logger.warning(f"Token estimate after truncation: {estimated_tokens_after:.0f}")

    try:
        prompt = (
            "You are a construction estimator tasked with extracting takeoff data from project documents. "
            "Analyze the provided text and identify takeoff items, including division, description, quantity, unit, unit cost, and modifier. "
            "Return a list of takeoff items in strict JSON format, where each item has the following fields: "
            "division (CSI division ID, e.g., '03'), description (item description), quantity (numeric), unit (e.g., 'sqft'), unitCost (numeric), modifier (percentage, default 0 if not specified). "
            "If no takeoff data is found, return an empty list. "
            "Respond in strict JSON format without markdown or extra text. Example structure:\n"
            "[{\"division\": \"03\", \"description\": \"Concrete slab\", \"quantity\": 1000, \"unit\": \"sqft\", \"unitCost\": 5.0, \"modifier\": 0}, ...]\n\n"
            f"Project Text:\n{full_text_truncated}\n\n"
            "Takeoff Items:"
        )
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a construction estimator with expertise in extracting takeoff data."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1000,
            temperature=0.5
        )
        takeoff_raw = response.choices[0].message.content.strip()
        if not takeoff_raw:
            logger.error("Empty response received from GPT for takeoff parsing")
            return JSONResponse(
                status_code=500,
                content={"detail": "Empty response received from GPT"}
            )
        logger.debug(f"Raw GPT response for takeoff: {takeoff_raw}")
        # Attempt to parse the response as JSON
        try:
            takeoff = json.loads(takeoff_raw)
            if not isinstance(takeoff, list):
                raise ValueError("Response is not a list")
        except json.JSONDecodeError:
            # Try to clean and parse
            takeoff_raw = takeoff_raw.replace("'", '"')
            try:
                takeoff = json.loads(takeoff_raw)
                if not isinstance(takeoff, list):
                    raise ValueError("Response is not a list")
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse takeoff response as JSON: {takeoff_raw}")
                return JSONResponse(
                    status_code=500,
                    content={"detail": f"Failed to parse takeoff response: {str(e)}"}
                )
        logger.info(f"Parsed {len(takeoff)} takeoff items")
        return {"takeoff": takeoff}
    except Exception as e:
        logger.error(f"Error parsing takeoff: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"detail": f"Error parsing takeoff: {str(e)}"}
        )

class DivisionAnalysisRequest(BaseModel):
    quotes: List[Dict]
    takeoff: List[Dict]
    specs: str

@app.post("/api/analyze-division/{division_id}")
async def analyze_division(division_id: str, request: DivisionAnalysisRequest):
    try:
        quotes = request.quotes
        takeoff = request.takeoff
        specs = request.specs

        if not quotes:
            logger.error(f"No quotes provided for division {division_id}")
            raise HTTPException(status_code=400, detail="Quotes are required")

        quote = quotes[0]  # Expecting a single quote for the division
        prompt = (
            "You are a construction project analyst using GPT-4o to analyze a specific CSI division for a construction project. "
            "Review the provided quote, takeoff items, and specifications for the division. "
            "Identify any potential issues, such as unusually low costs, missing takeoff items, or discrepancies between specs and takeoff. "
            "Return a dictionary with a 'quote' field containing any warnings about the quote (e.g., 'Cost seems unusually low'), "
            "and a 'takeoff' field containing warnings about takeoff items (e.g., 'Quantity seems off'). "
            "If no issues are found, return empty strings for each field. "
            "Respond in strict JSON format without markdown or extra text. Example structure:\n"
            "{\"quote\": \"Cost seems unusually low\", \"takeoff\": \"Quantity seems off\"}\n\n"
            f"Division ID: {division_id}\n"
            f"Quote: {json.dumps(quote)}\n"
            f"Takeoff Items: {json.dumps(takeoff)}\n"
            f"Specifications: {specs}\n\n"
            "Analysis:"
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
        if not analysis_raw:
            logger.error("Empty response received from GPT for division analysis")
            return JSONResponse(
                status_code=500,
                content={"detail": "Empty response received from GPT"}
            )
        logger.debug(f"Raw GPT response for division analysis: {analysis_raw}")
        try:
            analysis = json.loads(analysis_raw)
            if not isinstance(analysis, dict):
                raise ValueError("Response is not a dictionary")
            if "quote" not in analysis or "takeoff" not in analysis:
                raise ValueError("Response must contain 'quote' and 'takeoff' fields")
        except json.JSONDecodeError:
            analysis_raw = analysis_raw.replace("'", '"')
            try:
                analysis = json.loads(analysis_raw)
                if not isinstance(analysis, dict):
                    raise ValueError("Response is not a dictionary")
                if "quote" not in analysis or "takeoff" not in analysis:
                    raise ValueError("Response must contain 'quote' and 'takeoff' fields")
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse analysis response as JSON: {analysis_raw}")
                return JSONResponse(
                    status_code=500,
                    content={"detail": f"Failed to parse analysis response: {str(e)}"}
                )
        logger.info(f"Analysis completed for division {division_id}: {analysis}")
        return {"warnings": analysis}
    except Exception as e:
        logger.error(f"Error analyzing division {division_id}: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"detail": f"Error analyzing division {division_id}: {str(e)}"}
        )

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
        if not reply:
            logger.error("Empty response received from GPT for chat")
            return JSONResponse(
                status_code=500,
                content={"detail": "Empty response received from GPT"}
            )
        logger.info(f"Chat response generated: {reply}")
        return {"reply": reply}
    except Exception as e:
        logger.error(f"Error in chat: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"detail": f"Error in chat: {str(e)}"}
        )