from fastapi import FastAPI, HTTPException, status, Depends
from pydantic import BaseModel, StringConstraints, field_validator
from typing import Annotated, Optional, Dict, Any
from fastapi.middleware.cors import CORSMiddleware
from typing import Literal
import sqlite3
import bcrypt
import logging
import uvicorn
import json
from datetime import date

app = FastAPI()

logger = logging.getLogger("trainify")
logger.setLevel(logging.INFO)

# Allow CORS for frontend dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust for production!
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = "trainify.db"

def get_db():
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        yield conn
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=f"Database connection error: {e}")
    finally:
        if conn:
            conn.close()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            age INTEGER,
            gender TEXT,
            height INTEGER,
            weight INTEGER,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            level TEXT
        )
        """
    )
    # new progress table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_name TEXT NOT NULL,
            exercise_name TEXT NOT NULL,
            date_exercised TEXT NOT NULL,
            reps INTEGER NOT NULL,
            duration INTEGER NOT NULL,
            pta_metrics TEXT
        )
        """
    )
    conn.commit()
    conn.close()

NameStr = Annotated[str, StringConstraints(max_length=100)]
UsernameStr = Annotated[str, StringConstraints(max_length=50)]

class SignupData(BaseModel):
    name: NameStr
    age: int
    gender: Literal['male', 'female', 'other']
    height: int
    weight: int
    username: UsernameStr
    password: str
    level: Literal['beginner', 'intermediate', 'advanced']

    @field_validator('gender', 'level', mode='before')
    def _lower_enum(cls, v: str) -> str:
        if isinstance(v, str):
            return v.lower()
        return v

    @field_validator('age', 'height', 'weight', mode='before')
    def _to_int(cls, v):
        if isinstance(v, str) and v.isdigit():
            return int(v)
        return v

class LoginInfo(BaseModel):
    username: UsernameStr
    password: str

class ProgressData(BaseModel):
    user_name: NameStr
    exercise_name: NameStr
    date_exercised: date
    reps: int
    duration: int  # seconds
    pta_metrics: Optional[Dict[str, Optional[float]]] = None

    @field_validator('reps', 'duration', mode='before')
    def _to_int(cls, v):
        if isinstance(v, str) and v.isdigit():
            return int(v)
        return v

@app.on_event("startup")
def on_startup():
    init_db()
    logger.info("Initialized sqlite DB")


class PTARequest(BaseModel):
    user_name: NameStr
    exercise_name: NameStr

@app.post("/pta")
def get_pta(data: PTARequest, db=Depends(get_db)):
    """
    return the stored pta_metrics for the latest entry matching the user/exercise.
    if nothing is found or the field is empty we return `pta_metrics: None`.
    """
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT pta_metrics
        FROM progress
        WHERE user_name = ? AND exercise_name = ?
        ORDER BY date_exercised DESC
        LIMIT 1
        """,
        (data.user_name, data.exercise_name),
    )
    row = cursor.fetchone()
    cursor.close()

    if not row or not row["pta_metrics"]:
        return {"pta_metrics": None}

    try:
        metrics = json.loads(row["pta_metrics"])
    except json.JSONDecodeError:
        metrics = None

    return {"pta_metrics": metrics}

@app.post("/register")
def register(data: SignupData, db=Depends(get_db)):
    logger.info(f"Attempting registration: {data}")
    cursor = db.cursor()
    gender = data.gender.lower()
    level = data.level.lower()
    if gender not in ["male", "female", "other"]:
        raise HTTPException(status_code=400, detail="Invalid gender")
    if level not in ["beginner", "intermediate", "advanced"]:
        raise HTTPException(status_code=400, detail="Invalid level")

    cursor.execute("SELECT id FROM users WHERE username = ?", (data.username,))
    if cursor.fetchone():
        raise HTTPException(status_code=400, detail="Username already exists")

    hashed_pw = bcrypt.hashpw(data.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    try:
        cursor.execute(
            """
            INSERT INTO users (name, age, gender, height, weight, username, password, level)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data.name,
                data.age,
                gender,
                data.height,
                data.weight,
                data.username,
                hashed_pw,
                level,
            ),
        )
        db.commit()
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail="Database error: " + str(e))
    finally:
        cursor.close()

    return {"message": "Registration successful!"}

@app.post("/login")
def login(data: LoginInfo, db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?", (data.username,))
    row = cursor.fetchone()
    cursor.close()
    if not row:
        raise HTTPException(status_code=400, detail="Invalid username or password")

    stored_pw = row["password"]
    if not bcrypt.checkpw(data.password.encode("utf-8"), stored_pw.encode("utf-8")):
        raise HTTPException(status_code=400, detail="Invalid username or password")

    return {"message": "Login successful!"}

@app.post("/save-progress")
def save_progress(data: ProgressData, db=Depends(get_db)):
    cursor = db.cursor()
    metrics_json = None
    if data.pta_metrics is not None:
        metrics_json = json.dumps(data.pta_metrics)

    try:
        cursor.execute(
            """
            INSERT INTO progress
                (user_name, exercise_name, date_exercised, reps, duration, pta_metrics)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                data.user_name,
                data.exercise_name,
                data.date_exercised.isoformat(),
                data.reps,
                data.duration,
                metrics_json,
            ),
        )
        db.commit()
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail="Database error: " + str(e))
    finally:
        cursor.close()

    return {"success": True, "message": "Progress saved"}

@app.get("/")
def read_root():
    return {"message": "Welcome to Trainify Backend!"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)