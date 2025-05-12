from fastapi import UploadFile, File
import shutil
import os
from pdf_parser import extract_structured_takeoff

@app.post("/api/parse-structured")
async def parse_structured(file: UploadFile = File(...)):
    temp_path = f"temp_uploads/{file.filename}"
    os.makedirs("temp_uploads", exist_ok=True)

    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        results = extract_structured_takeoff(temp_path)
        return {"parsed": results}
    finally:
        os.remove(temp_path)
