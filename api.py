from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sqlite3
from typing import List
import os
from dotenv import load_dotenv
import requests

load_dotenv()

WEBHOOK_URL = os.getenv("WEBHOOK_URL")
DATABASE_URL = os.getenv("DATABASE_URL", "quotes.db")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Quote(BaseModel):
    id: int
    text: str
    author: str

class UserProfile(BaseModel):
    id: int
    name: str
    email: str
    favorite_quotes: List[int]

class Deviation(BaseModel):
    id: int
    user_id: int
    deviation: float
    timestamp: str


@app.get("/")
def read_root():
    return {"Hello": "World"}

@app.get("/quotes", response_model=List[Quote])
def get_quotes():
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute("SELECT id, text, author FROM quotes")
    quotes = cursor.fetchall()
    conn.close()
    return [Quote(id=quote[0], text=quote[1], author=quote[2]) for quote in quotes]

@app.post("/user-profile", response_model=UserProfile)
def create_user_profile(user_profile: UserProfile):
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO user_profiles (id, name, email) VALUES (?, ?, ?)", (user_profile.id, user_profile.name, user_profile.email))
    conn.commit()
    cursor.execute("SELECT id, name, email FROM user_profiles WHERE id = ?", (user_profile.id,))
    user = cursor.fetchone()
    conn.close()
    return UserProfile(id=user[0], name=user[1], email=user[2])

@app.post("/deviation", response_model=Deviation)
def create_deviation(deviation: Deviation):
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO deviations (user_id, deviation, timestamp) VALUES (?, ?, ?)", (deviation.user_id, deviation.deviation, deviation.timestamp))
    conn.commit()
    cursor.execute("SELECT id, user_id, deviation, timestamp FROM deviations WHERE id = (SELECT MAX(id) FROM deviations)")
    dev = cursor.fetchone()
    conn.close()
    return Deviation(id=dev[0], user_id=dev[1], deviation=dev[2], timestamp=dev[3])

@app.post("/webhook")
def trigger_webhook(data: dict):
    response = requests.post(WEBHOOK_URL, json=data)
    if response.status_code != 200:
        raise HTTPException(status_code=400, detail="Webhook failed")
    return {"status": "success"}


# Initialize the database if it doesn't exist
def init_db():
        try:
conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS quotes (id INTEGER PRIMARY KEY, text TEXT, author TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS user_profiles (id INTEGER PRIMARY KEY, name TEXT, email TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS deviations (id INTEGER PRIMARY KEY, user_id INTEGER, deviation REAL, timestamp TEXT)")
    conn.commit()
    except Exception as e:
        print(f"Error initializing database: {e}")

    conn.close()

init_db()
