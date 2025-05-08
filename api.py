from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import pandas as pd
import io
import sqlite3
import logging
import uuid
from datetime import datetime, timedelta
import fitz  # PyMuPDF
import re
import requests
import os
from dotenv import load_dotenv

# Load environmental variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO if os.getenv("DEBUG", "False").lower() != "true" else logging.DEBUG)

# Configure separate logging for parsing
parse_logger = logging.getLogger('quote_parsing')
parse_handler = logging.FileHandler('quote_parsing.log')
parse_handler.setLevel(logging.INFO)
parse_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
parse_handler.setFormatter(parse_formatter)
parse_logger.addHandler(parse_handler)

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load environmental variables
ZAPIER_WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://hooks.zapier.com/hooks/catch/22782499/2nor5fy/")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///quotes.db")  # Default to SQLite for now
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "sk_test_placeholder")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "whsec_placeholder")
STRIPE_PRO_PRICE_ID = os.getenv("STRIPE_PRO_PRICE_ID", "price_pro_placeholder")
STRIPE_TEAM_PRICE_ID = os.getenv("STRIPE_TEAM_PRICE_ID", "price_team_placeholder")

# SQLite setup (temporary until PostgreSQL is integrated)
def init_db():
    conn = sqlite3.connect('quotes.db')
    c = conn.cursor()
    # Quotes table
    c.execute('''CREATE TABLE IF NOT EXISTS quotes
                 (id TEXT PRIMARY KEY, profile_id TEXT, nickname TEXT, region TEXT, scope TEXT, unit_price REAL, unit TEXT,
                  flag TEXT, suggested_action TEXT, deviation_pct TEXT, timestamp TEXT, source TEXT, file_name TEXT,
                  action_timestamp TEXT, parsed_correctly BOOLEAN)''')
    # User profiles table
    c.execute('''CREATE TABLE IF NOT EXISTS user_profiles
                 (id TEXT PRIMARY KEY, nickname TEXT, self_perform_concrete TEXT, electrical_markup_target TEXT,
                  vendor_behavior TEXT, coastal_focus_pct INTEGER, tolerance_threshold INTEGER, created_at TEXT,
                  user_tier TEXT, onboarding_completed BOOLEAN, last_qa_screen INTEGER, first_quote_timestamp TEXT,
                  subscription_expiry TEXT, project_type TEXT, building_type TEXT, labor_type TEXT, target_margin TEXT,
                  site_conditions TEXT)''')
    # Deviations table for logging deviation statistics
    c.execute('''CREATE TABLE IF NOT EXISTS deviations
                 (id TEXT PRIMARY KEY, scope TEXT, deviation_pct REAL, timestamp TEXT)''')
    conn.commit()
    conn.close()

init_db()

def validate_quotes(csv_file_path):
    """
    Validates quotes from a CSV file by comparing unit prices against baseline values.
    Returns a DataFrame with added columns: flag, suggested_action, deviation_pct.
    """
    try:
        df = pd.read_csv(csv_file_path)
        
        # Define baseline prices (placeholders until RSMeans data is available)
        baseline_prices = {
            "Doors": {"price": 500.0, "unit": "Unit"},
            "Concrete Slab": {"price": 0.12, "unit": "SF"},  # Simplified conversion: $120/CY ≈ $0.12/SF
            "MEP": {"price": 1000.0, "unit": "Unit"},
            "Architectural": {"price": 1500.0, "unit": "Unit"},
            "Windows": {"price": 300.0, "unit": "Unit"},
            "Ceiling": {"price": 200.0, "unit": "Unit"},
            "Finish": {"price": 100.0, "unit": "Unit"}
        }

        # Initialize new columns
        df['flag'] = 'Normal'
        df['suggested_action'] = 'Accept'
        df['deviation_pct'] = 0.0

        for index, row in df.iterrows():
            scope = row['scope']
            unit_price = row['unit_price']
            unit = row['unit']

            # Extract the base scope (e.g., "Ceiling - Acoustic Panels" -> "Ceiling")
            base_scope = scope.split(" - ")[0] if " - " in scope else scope
            base_scope = base_scope.split("Finish - ")[1] if scope.startswith("Finish - ") else base_scope

            # Check if the scope matches a baseline
            if base_scope in baseline_prices:
                baseline = baseline_prices[base_scope]["price"]
                baseline_unit = baseline_prices[base_scope]["unit"]

                # Ensure units match (simplified for now)
                if unit == baseline_unit:
                    # Calculate deviation percentage
                    deviation_pct = ((unit_price - baseline) / baseline) * 100
                    df.at[index, 'deviation_pct'] = f"{deviation_pct:.2f}%"

                    # Set flag and suggested action based on deviation
                    deviation = abs(deviation_pct)
                    if deviation > 20:
                        df.at[index, 'flag'] = 'High'
                        df.at[index, 'suggested_action'] = 'Review'
                    elif deviation > 10:
                        df.at[index, 'flag'] = 'Moderate'
                        df.at[index, 'suggested_action'] = 'Review'
                    else:
                        df.at[index, 'flag'] = 'Normal'
                        df.at[index, 'suggested_action'] = 'Accept'
                else:
                    df.at[index, 'flag'] = 'Error'
                    df.at[index, 'suggested_action'] = 'Review'
                    df.at[index, 'deviation_pct'] = 'N/A'
            else:
                # Unknown scope
                df.at[index, 'flag'] = 'Unknown'
                df.at[index, 'suggested_action'] = 'Review'
                df.at[index, 'deviation_pct'] = 'N/A'

        return df
    except Exception as e:
        logging.error(f"Error validating quotes: {str(e)}")
        return None

def parse_pdf(file_path):
    try:
        doc = fitz.open(file_path)
        extracted_data = {
            "door_count": 0,
            "slab_sf": 0,
            "scopes": {},
            "schedule_tables": [],
            "windows": 0,
            "ceiling_types": {},
            "finish_schedules": {}
        }

        # Expanded regex patterns for various elements
        door_pattern = re.compile(r"(?:door\s*schedule|floor\s*plan|doors?):?\s*(\d+)\s*(?:units|doors)?", re.IGNORECASE)
        slab_pattern = re.compile(r"(?:slab|foundation|concrete\s*slab|slab\s*area):?\s*(\d{1,3}(?:,\d{3})*)\s*(?:sf|square\s*feet)", re.IGNORECASE)
        scope_pattern = re.compile(r"(MEP|Architectural|Structural)\s*:\s*([^\n]+)", re.IGNORECASE)
        table_pattern = re.compile(r"(?i)(?:schedule|table)\s*of\s*(?:quantities|materials).*?(?:\n\s*([^\n]+))+", re.DOTALL)
        window_pattern = re.compile(r"(?:window\s*schedule|windows?):?\s*(\d+)\s*(?:units|windows)?", re.IGNORECASE)
        ceiling_pattern = re.compile(r"(?:ceiling\s*type|ceiling\s*schedule|ceiling):?\s*([^\n]+)", re.IGNORECASE)
        finish_pattern = re.compile(r"(?:finish\s*schedule|finishes):?\s*([^\n]+)", re.IGNORECASE)

        for page_num, page in enumerate(doc):
            text = page.get_text("text")
            parse_logger.info(f"Processing page {page_num + 1} of {file_path}")
            parse_logger.info(f"Page text: {text}")

            # Door counts (schedules, floor plans)
            door_match = door_pattern.search(text)
            if door_match:
                extracted_data["door_count"] = int(door_match.group(1))
                parse_logger.info(f"Page {page_num + 1}: Extracted door count: {extracted_data['door_count']}")
            else:
                parse_logger.warning(f"Page {page_num + 1}: No door count found")

            # Slab square footage (notes, structural sheets, dimensioned plans)
            slab_match = slab_pattern.search(text)
            if slab_match:
                extracted_data["slab_sf"] = int(slab_match.group(1).replace(',', ''))
                parse_logger.info(f"Page {page_num + 1}: Extracted slab SF: {extracted_data['slab_sf']}")
            else:
                parse_logger.warning(f"Page {page_num + 1}: No slab SF found - may require custom logic or OCR fallback")

            # Scope labeling (MEP, Architectural, etc.)
            scope_matches = scope_pattern.findall(text)
            for scope_type, scope_value in scope_matches:
                extracted_data["scopes"][scope_type] = scope_value.strip()
                parse_logger.info(f"Page {page_num + 1}: Extracted scope {scope_type}: {scope_value.strip()}")
            if not scope_matches:
                parse_logger.warning(f"Page {page_num + 1}: No scope labels found")

            # Window counts
            window_match = window_pattern.search(text)
            if window_match:
                extracted_data["windows"] = int(window_match.group(1))
                parse_logger.info(f"Page {page_num + 1}: Extracted window count: {extracted_data['windows']}")
            else:
                parse_logger.warning(f"Page {page_num + 1}: No window count found")

            # Ceiling types
            ceiling_matches = ceiling_pattern.findall(text)
            for ceiling_type in ceiling_matches:
                extracted_data["ceiling_types"][ceiling_type] = ceiling_type.strip()
                parse_logger.info(f"Page {page_num + 1}: Extracted ceiling type: {ceiling_type.strip()}")
            if not ceiling_matches:
                parse_logger.warning(f"Page {page_num + 1}: No ceiling types found")

            # Finish schedules (with normalization for "ROOM")
            finish_matches = finish_pattern.findall(text)
            for finish in finish_matches:
                finish_cleaned = "Interior Room" if finish.strip().upper() == "ROOM" else finish.strip()
                extracted_data["finish_schedules"][finish_cleaned] = finish_cleaned
                parse_logger.info(f"Page {page_num + 1}: Extracted finish schedule: {finish_cleaned} (original: {finish.strip()})")
            if not finish_matches:
                parse_logger.warning(f"Page {page_num + 1}: No finish schedules found - may require custom logic or OCR fallback")

            # Embedded schedule tables
            table_match = table_pattern.search(text)
            if table_match:
                table_data = table_match.group(0).split('\n')[1:]  # Skip header
                table_data = [line.strip() for line in table_data if line.strip()]
                extracted_data["schedule_tables"].extend(table_data)
                parse_logger.info(f"Page {page_num + 1}: Extracted schedule table: {table_data}")
            else:
                parse_logger.warning(f"Page {page_num + 1}: No schedule table found - may require custom logic or OCR fallback")

        doc.close()
        return extracted_data
    except Exception as e:
        parse_logger.error(f"Error parsing PDF {file_path}: {str(e)}")
        raise

def convert_to_quotes(data, profile_id, nickname):
    quotes = []
    # Door counts
    if data["door_count"] > 0:
        quotes.append({
            "profile_id": profile_id,
            "nickname": nickname,
            "region": "NC",
            "scope": "Doors",
            "unit_price": 500.0,  # Specified price: $500/door
            "unit": "Unit",
            "timestamp": datetime.utcnow().isoformat(),
            "source": "PDF Parsed",
            "parsed_correctly": None
        })
    # Slab square footage
    if data["slab_sf"] > 0:
        quotes.append({
            "profile_id": profile_id,
            "nickname": nickname,
            "region": "NC",
            "scope": "Concrete Slab",
            "unit_price": data["slab_sf"] * 0.12,  # Specified price: $0.12/SF
            "unit": "SF",
            "timestamp": datetime.utcnow().isoformat(),
            "source": "PDF Parsed",
            "parsed_correctly": None
        })
    # Scopes (MEP, Architectural)
    for scope_type, scope_value in data["scopes"].items():
        if "MEP" in scope_type.upper():
            quotes.append({
                "profile_id": profile_id,
                "nickname": nickname,
                "region": "NC",
                "scope": "MEP",
                "unit_price": 1000.0,  # Placeholder
                "unit": "Unit",
                "timestamp": datetime.utcnow().isoformat(),
                "source": "PDF Parsed",
                "parsed_correctly": None
            })
        elif "ARCHITECTURAL" in scope_type.upper():
            quotes.append({
                "profile_id": profile_id,
                "nickname": nickname,
                "region": "NC",
                "scope": "Architectural",
                "unit_price": 1500.0,  # Placeholder
                "unit": "Unit",
                "timestamp": datetime.utcnow().isoformat(),
                "source": "PDF Parsed",
                "parsed_correctly": None
            })
    # Window counts
    if data["windows"] > 0:
        quotes.append({
            "profile_id": profile_id,
            "nickname": nickname,
            "region": "NC",
            "scope": "Windows",
            "unit_price": 300.0,  # Placeholder
            "unit": "Unit",
            "timestamp": datetime.utcnow().isoformat(),
            "source": "PDF Parsed",
            "parsed_correctly": None
        })
    # Ceiling types (placeholder logic)
    for ceiling_type in data["ceiling_types"].values():
        quotes.append({
            "profile_id": profile_id,
            "nickname": nickname,
            "region": "NC",
            "scope": f"Ceiling - {ceiling_type}",
            "unit_price": 200.0,  # Placeholder
            "unit": "Unit",
            "timestamp": datetime.utcnow().isoformat(),
            "source": "PDF Parsed",
            "parsed_correctly": None
        })
    # Finish schedules
    for finish in data["finish_schedules"].values():
        quotes.append({
            "profile_id": profile_id,
            "nickname": nickname,
            "region": "NC",
            "scope": f"Finish - {finish}",
            "unit_price": 100.0,  # Placeholder
            "unit": "Unit",
            "timestamp": datetime.utcnow().isoformat(),
            "source": "PDF Parsed",
            "parsed_correctly": None
        })
    # Schedule tables (placeholder logic)
    for table_entry in data["schedule_tables"]:
        if "window" in table_entry.lower():
            quotes.append({
                "profile_id": profile_id,
                "nickname": nickname,
                "region": "NC",
                "scope": "Windows",
                "unit_price": 300.0,  # Placeholder
                "unit": "Unit",
                "timestamp": datetime.utcnow().isoformat(),
                "source": "PDF Parsed",
                "parsed_correctly": None
            })
    return quotes

@app.get("/upload", response_class=HTMLResponse)
async def serve_upload_page():
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>Estimator GPT - Upload Quote</title>
      <script src="https://cdn.tailwindcss.com"></script>
      <style>
        body { font-family: Arial, sans-serif; }
        .section { padding: 4rem 2rem; }
        .upload-section { background-color: #f9fafb; text-align: center; }
      </style>
    </head>
    <body>
      <div class="upload-section section">
        <h2 class="text-3xl font-bold mb-4">Upload Your Quote</h2>
        <p class="text-lg mb-6">Upload a PDF or CSV to validate your quote against regional benchmarks.</p>
        <input type="file" id="quote-file" accept=".csv,.pdf" class="border p-2 w-full max-w-md mb-4 rounded" aria-label="Upload CSV or PDF file">
        <button onclick="validateQuote()" class="bg-blue-500 text-white px-6 py-3 rounded-lg text-lg hover:bg-blue-600">Validate Quote</button>
      </div>
      <script>
        async function validateQuote() {
          const fileInput = document.getElementById('quote-file');
          if (!fileInput.files.length) {
            alert('Please upload a CSV or PDF file.');
            return;
          }

          const formData = new FormData();
          formData.append('file', fileInput.files[0]);
          formData.append('profile_id', 'test-profile-id');
          formData.append('nickname', 'Test Contractor');

          const fileExtension = fileInput.files[0].name.split('.').pop().toLowerCase();
          const endpoint = fileExtension === 'pdf' ? '/api/parse-pdf' : '/api/validate';

          try {
            const response = await fetch(endpoint, {
              method: 'POST',
              body: formData
            });
            const data = await response.json();
            if (data.error) {
              alert(data.error);
            } else {
              alert('Quote validated successfully!');
            }
          } catch (error) {
            alert('Error validating quote: ' + error.message);
          }
        }
      </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.post("/api/validate")
async def validate(file: UploadFile = File(...), profile_id: str = None):
    logging.info("Starting quote validation process")
    
    # Tier enforcement
    if profile_id:
        conn = sqlite3.connect('quotes.db')
        c = conn.cursor()
        c.execute('''SELECT user_tier, subscription_expiry, first_quote_timestamp FROM user_profiles WHERE id = ?''',
                  (profile_id,))
        user = c.fetchone()
        
        if user:
            user_tier, subscription_expiry, first_quote_timestamp = user
            
            if user_tier == "Beta" and first_quote_timestamp is None:
                expiry_date = (datetime.utcnow() + timedelta(days=90)).isoformat()
                c.execute('''UPDATE user_profiles SET subscription_expiry = ? WHERE id = ?''',
                          (expiry_date, profile_id))
                conn.commit()
            
            if user_tier == "Free":
                c.execute('''SELECT COUNT(*) FROM quotes WHERE profile_id = ? AND timestamp >= ?''',
                          (profile_id, datetime.utcnow().replace(day=1).isoformat()))
                quote_count = c.fetchone()[0]
                if quote_count >= 1:
                    conn.close()
                    raise HTTPException(status_code=403, detail="Free Tier limit reached. Upgrade to Pro for unlimited estimates.")
        
        conn.close()

    content = await file.read()
    df = pd.read_csv(io.StringIO(content.decode('utf-8')))

    temp_file = "temp_quotes.csv"
    df.to_csv(temp_file, index=False)

    validated_df = validate_quotes(temp_file)

    if validated_df is None:
        return {"error": "Failed to validate quotes. Check file format."}

    result = validated_df.to_dict(orient='records')
    quotes = []
    for row in result:
        quote_id = str(uuid.uuid4())
        quote = {
            "id": quote_id,
            "profile_id": profile_id,
            "nickname": "Builder",
            "region": row['region'],
            "scope": row['scope'],
            "unit_price": row['unit_price'],
            "unit": row['unit'],
            "flag": row['flag'],
            "suggested_action": row['suggested_action'],
            "deviation_pct": row['deviation_pct'],
            "timestamp": row['date'],
            "source": "CSV Upload",
            "file_name": file.filename,
            "action_timestamp": None,
            "parsed_correctly": None
        }
        quotes.append(quote)

        # Store in SQLite (quotes table)
        conn = sqlite3.connect('quotes.db')
        c = conn.cursor()
        c.execute('''INSERT INTO quotes (id, profile_id, nickname, region, scope, unit_price, unit, flag, suggested_action,
                  deviation_pct, timestamp, source, file_name, action_timestamp, parsed_correctly)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (quote_id, profile_id, quote["nickname"], quote["region"], quote["scope"], quote["unit_price"],
                   quote["unit"], quote["flag"], quote["suggested_action"], quote["deviation_pct"],
                   quote["timestamp"], quote["source"], quote["file_name"], quote["action_timestamp"], None))
        
        # Log deviation in deviations table
        deviation_pct = float(row['deviation_pct'].replace('%', '')) if row['deviation_pct'] and row['deviation_pct'] != 'N/A' else 0.0
        deviation_id = str(uuid.uuid4())
        c.execute('''INSERT INTO deviations (id, scope, deviation_pct, timestamp)
                     VALUES (?, ?, ?, ?)''',
                  (deviation_id, row['scope'], deviation_pct, datetime.utcnow().isoformat()))
        
        # Update first_quote_timestamp and subscription_expiry
        if profile_id:
            c.execute('''SELECT first_quote_timestamp, user_tier FROM user_profiles WHERE id = ?''', (profile_id,))
            user = c.fetchone()
            if user:
                first_quote_timestamp, user_tier = user
                if first_quote_timestamp is None:
                    first_quote_timestamp = datetime.utcnow().isoformat()
                    c.execute('''UPDATE user_profiles SET first_quote_timestamp = ? WHERE id = ?''',
                              (first_quote_timestamp, profile_id))
                    if user_tier == "Beta":
                        expiry_date = (datetime.utcnow() + timedelta(days=90)).isoformat()
                        c.execute('''UPDATE user_profiles SET subscription_expiry = ? WHERE id = ?''',
                                  (expiry_date, profile_id))
        
        conn.commit()
        conn.close()

    logging.info(f"Validated {len(validated_df)} quotes")
    return {"quotes": quotes}

@app.post("/api/parse-pdf")
async def parse_pdf_endpoint(file: UploadFile = File(...), profile_id: str = None, nickname: str = "Builder"):
    logging.info("Starting PDF parsing process")
    parse_logger.info(f"Received PDF upload: {file.filename}")
    content = await file.read()
    temp_file = "temp_plan.pdf"
    with open(temp_file, "wb") as f:
        f.write(content)

    extracted_data = parse_pdf(temp_file)
    parse_logger.info(f"Extracted data from {file.filename}: {extracted_data}")
    quotes = convert_to_quotes(extracted_data, profile_id, nickname)

    if not quotes:
        parse_logger.warning(f"No relevant data extracted from {file.filename}")
        return {"error": "No relevant data extracted from PDF."}

    df = pd.DataFrame(quotes)
    temp_csv = "temp_quotes.csv"
    df.to_csv(temp_csv, index=False)

    validated_df = validate_quotes(temp_csv)

    if validated_df is None:
        parse_logger.error(f"Failed to validate parsed quotes from {file.filename}")
        return {"error": "Failed to validate parsed quotes."}

    result = validated_df.to_dict(orient='records')
    quotes = []
    for row in result:
        quote_id = str(uuid.uuid4())
        quote = {
            "id": quote_id,
            "profile_id": profile_id,
            "nickname": nickname,
            "region": row['region'],
            "scope": row['scope'],
            "unit_price": row['unit_price'],
            "unit": row['unit'],
            "flag": row['flag'],
            "suggested_action": row['suggested_action'],
            "deviation_pct": row['deviation_pct'],
            "timestamp": row['timestamp'],
            "source": row['source'],
            "file_name": file.filename,
            "action_timestamp": None,
            "parsed_correctly": None
        }
        quotes.append(quote)

        # Store in SQLite (quotes table)
        conn = sqlite3.connect('quotes.db')
        c = conn.cursor()
        c.execute('''INSERT INTO quotes (id, profile_id, nickname, region, scope, unit_price, unit, flag, suggested_action,
                  deviation_pct, timestamp, source, file_name, action_timestamp, parsed_correctly)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (quote_id, profile_id, nickname, quote["region"], quote["scope"], quote["unit_price"],
                   quote["unit"], quote["flag"], quote["suggested_action"], quote["deviation_pct"],
                   quote["timestamp"], quote["source"], quote["file_name"], quote["action_timestamp"], None))
        
        # Log deviation in deviations table
        deviation_pct = float(row['deviation_pct'].replace('%', '')) if row['deviation_pct'] and row['deviation_pct'] != 'N/A' else 0.0
        deviation_id = str(uuid.uuid4())
        c.execute('''INSERT INTO deviations (id, scope, deviation_pct, timestamp)
                     VALUES (?, ?, ?, ?)''',
                  (deviation_id, row['scope'], deviation_pct, datetime.utcnow().isoformat()))
        
        if profile_id:
            c.execute('''SELECT first_quote_timestamp, user_tier FROM user_profiles WHERE id = ?''', (profile_id,))
            user = c.fetchone()
            if user:
                first_quote_timestamp, user_tier = user
                if first_quote_timestamp is None:
                    first_quote_timestamp = datetime.utcnow().isoformat()
                    c.execute('''UPDATE user_profiles SET first_quote_timestamp = ? WHERE id = ?''',
                              (first_quote_timestamp, profile_id))
                    if user_tier == "Beta":
                        expiry_date = (datetime.utcnow() + timedelta(days=90)).isoformat()
                        c.execute('''UPDATE user_profiles SET subscription_expiry = ? WHERE id = ?''',
                                  (expiry_date, profile_id))
        
        conn.commit()
        conn.close()

    logging.info(f"Parsed and validated {len(quotes)} quotes from PDF")
    return {"quotes": quotes}

@app.patch("/api/update-quote/{quote_id}")
async def update_quote(quote_id: str, suggested_action: str, parsed_correctly: bool = None):
    action_timestamp = datetime.utcnow().isoformat()
    conn = sqlite3.connect('quotes.db')
    c = conn.cursor()
    c.execute('''UPDATE quotes SET suggested_action = ?, action_timestamp = ?, parsed_correctly = ? WHERE id = ?''',
              (suggested_action, action_timestamp, parsed_correctly, quote_id))
    if c.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Quote not found")
    conn.commit()
    conn.close()
    logging.info(f"Updated quote {quote_id} with action {suggested_action} and parsed_correctly {parsed_correctly}")
    return {"id": quote_id, "suggested_action": suggested_action, "parsed_correctly": parsed_correctly, "action_timestamp": action_timestamp}

@app.post("/api/save-profile")
async def save_profile(profile: dict):
    profile_id = str(uuid.uuid4())
    created_at = datetime.utcnow().isoformat()
    nickname = profile.get('nickname', 'Builder')
    last_qa_screen = profile.get('last_qa_screen', 7)

    # Simulate email for now (can be collected in Q&A or user signup)
    email = profile.get('email', 'builder@example.com')

    conn = sqlite3.connect('quotes.db')
    c = conn.cursor()
    c.execute('''INSERT INTO user_profiles (id, nickname, self_perform_concrete, electrical_markup_target,
                 vendor_behavior, coastal_focus_pct, tolerance_threshold, created_at, user_tier, onboarding_completed,
                 last_qa_screen, first_quote_timestamp, subscription_expiry, project_type, building_type, labor_type,
                 target_margin, site_conditions)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
              (profile_id, nickname, profile.get('self_perform_concrete'), profile.get('electrical_markup_target'),
               profile.get('vendor_behavior'), int(profile.get('coastal_focus_pct', 0)),
               int(profile.get('tolerance_threshold', 20)), created_at, "Beta", True, last_qa_screen, None, None,
               profile.get('project_type'), profile.get('building_type'), profile.get('labor_type'),
               profile.get('target_margin'), profile.get('site_conditions')))
    conn.commit()
    conn.close()

    # Send webhook to Zapier
    webhook_payload = {
        "nickname": nickname,
        "email": email,
        "upload_link": "https://estimator-gpt.com/upload",
        "support_link": "https://estimator-gpt.com/support"
    }
    try:
        response = requests.post(ZAPIER_WEBHOOK_URL, json=webhook_payload)
        response.raise_for_status()
        logging.info(f"Sent webhook to Zapier for profile {profile_id}: {response.status_code}")
    except requests.RequestException as e:
        logging.error(f"Failed to send webhook to Zapier for profile {profile_id}: {str(e)}")

    logging.info(f"Saved profile for {nickname} with ID {profile_id}")
    return {"profile_id": profile_id}

@app.get("/api/profiles")
async def get_profiles():
    conn = sqlite3.connect('quotes.db')
    c = conn.cursor()
    c.execute('''SELECT * FROM user_profiles''')
    profiles = c.fetchall()
    conn.close()

    columns = ['id', 'nickname', 'self_perform_concrete', 'electrical_markup_target', 'vendor_behavior',
               'coastal_focus_pct', 'tolerance_threshold', 'created_at', 'user_tier', 'onboarding_completed',
               'last_qa_screen', 'first_quote_timestamp', 'subscription_expiry', 'project_type', 'building_type',
               'labor_type', 'target_margin', 'site_conditions']
    return [dict(zip(columns, profile)) for profile in profiles]

@app.get("/api/deviation-histogram")
async def get_deviation_histogram():
    conn = sqlite3.connect('quotes.db')
    c = conn.cursor()
    c.execute('''SELECT scope, deviation_pct FROM deviations''')
    deviations = c.fetchall()
    conn.close()

    histogram = {}
    for scope, deviation in deviations:
        if scope not in histogram:
            histogram[scope] = {"<10%": 0, "10-20%": 0, ">20%": 0}
        if deviation < 10:
            histogram[scope]["<10%"] += 1
        elif 10 <= deviation <= 20:
            histogram[scope]["10-20%"] += 1
        else:
            histogram[scope][">20%"] += 1

    return histogram

@app.post("/api/subscribe")
async def subscribe(profile_id: str, plan: str):
    if plan not in ["Free", "Pro", "Team"]:
        raise HTTPException(status_code=400, detail="Invalid plan type")

    # Configure Stripe
    import stripe
    stripe.api_key = STRIPE_SECRET_KEY

    price_ids = {
        "Pro": STRIPE_PRO_PRICE_ID,
        "Team": STRIPE_TEAM_PRICE_ID
    }

    if plan == "Free":
        expiry_date = None
    else:
        try:
            session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{
                    'price': price_ids[plan],
                    'quantity': 1,
                }],
                mode='subscription',
                success_url='https://estimator-gpt.com/success',
                cancel_url='https://estimator-gpt.com/cancel',
                metadata={'profile_id': profile_id, 'plan': plan}
            )
            return {"session_id": session.id, "url": session.url}
        except stripe.error.StripeError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # Update user_tier for Free plan immediately
    conn = sqlite3.connect('quotes.db')
    c = conn.cursor()
    c.execute('''UPDATE user_profiles SET user_tier = ?, subscription_expiry = ? WHERE id = ?''',
              (plan, expiry_date, profile_id))
    if c.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Profile not found")
    conn.commit()
    conn.close()

    logging.info(f"Upgraded profile {profile_id} to {plan} plan")
    return {"status": "success", "plan": plan, "subscription_expiry": expiry_date}

@app.post("/api/stripe-webhook")
async def stripe_webhook(request: dict):
    event = request
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        profile_id = session['metadata']['profile_id']
        plan = session['metadata']['plan']
        expiry_date = (datetime.utcnow() + timedelta(days=30)).isoformat()

        conn = sqlite3.connect('quotes.db')
        c = conn.cursor()
        c.execute('''UPDATE user_profiles SET user_tier = ?, subscription_expiry = ? WHERE id = ?''',
                  (plan, expiry_date, profile_id))
        if c.rowcount == 0:
            conn.close()
            raise HTTPException(status_code=404, detail="Profile not found")
        conn.commit()
        conn.close()

        logging.info(f"Stripe webhook: Upgraded profile {profile_id} to {plan} plan")
    return {"status": "success"}