from fastapi import FastAPI, UploadFile, File
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os
import requests
import sqlite3
from datetime import datetime
from dotenv import load_dotenv
import fitz  # PyMuPDF

# Load .env variables
load_dotenv()

app = FastAPI()

# Enable frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# === DATABASE SETUP ===
DB_FILE = "quotes.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS quote_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quote_id INTEGER,
            action TEXT,
            action_timestamp TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# === MODELS ===

class QuoteFeedback(BaseModel):
    quote_id: int
    action:
