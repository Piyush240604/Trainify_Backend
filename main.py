from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, StringConstraints, field_validator
from typing import Annotated, List, Optional, Dict, Literal
from datetime import date, datetime, timedelta, timezone
import sqlite3
import bcrypt
import logging
import uvicorn
import json
import jwt
import sys

# --- CONFIGURATION ---
SECRET_KEY = "Trainify-key"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30
DB_PATH = "trainify.db"

# --- LOGGING SETUP ---
logger = logging.getLogger("trainify")
logger.setLevel(logging.INFO)
logger.propagate = False
if logger.hasHandlers():
    logger.handlers.clear()
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
logger.addHandler(handler)

# --- UTILITIES ---
def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

# --- DATABASE SETUP ---
def get_db():
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        yield conn
    except sqlite3.Error as e:
        logger.error(f"Database connection error: {e}")
        raise HTTPException(status_code=500, detail=f"Database connection error: {e}")
    finally:
        if conn:
            conn.close()

def init_db():
    logger.info("Initializing database...")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        cur.execute("""
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
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_name TEXT NOT NULL,
            exercise_name TEXT NOT NULL,
            date_exercised TEXT NOT NULL,
            reps INTEGER NOT NULL,
            duration INTEGER NOT NULL
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS pta (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            exercise_name TEXT NOT NULL,
            metrics TEXT,
            is_active INTEGER DEFAULT 1,
            updated_at TEXT,
            UNIQUE(username, exercise_name)
        )
        """)
        conn.commit()
    except Exception as e:
        logger.error(f"Database init error: {e}")
    finally:
        conn.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield
    
app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- PYDANTIC MODELS ---
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
        return v.lower() if isinstance(v, str) else v

    @field_validator('age', 'height', 'weight', mode='before')
    def _to_int(cls, v):
        return int(v) if isinstance(v, str) and v.isdigit() else v

class LoginInfo(BaseModel):
    username: UsernameStr
    password: str

class ProgressData(BaseModel):
    user_name: NameStr
    exercise_name: NameStr
    date_exercised: date
    reps: int
    duration: int
    pta_metrics: Optional[Dict[str, float]] = None

    @field_validator('reps', 'duration', mode='before')
    def _to_int(cls, v):
        return int(v) if isinstance(v, str) and v.isdigit() else v

class PTARequest(BaseModel):
    user_name: NameStr
    exercise_name: NameStr

class StatsBlock(BaseModel):
    workouts: int
    reps: int
    duration: int

class LastWorkout(BaseModel):
    exercise: Optional[str]
    date: Optional[str]
    reps: int
    duration: int

class StatsResponse(BaseModel):
    totals: StatsBlock
    weekly: StatsBlock
    monthly: StatsBlock
    last_workout: LastWorkout
    worked_days: List[int]

# --- ENDPOINTS ---
@app.get("/")
def read_root():
    return {"message": "Welcome to Trainify Backend!"}

@app.post("/register")
def register(data: SignupData, db=Depends(get_db)):
    logger.info(f"[/register] Processing registration for: {data.username}")
    cursor = db.cursor()
    
    try:
        cursor.execute("SELECT id FROM users WHERE username = ?", (data.username,))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Username already exists")

        hashed_pw = bcrypt.hashpw(data.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

        cursor.execute(
            """
            INSERT INTO users (name, age, gender, height, weight, username, password, level)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (data.name, data.age, data.gender, data.height, data.weight, data.username, hashed_pw, data.level),
        )
        db.commit()
        return {"message": "Registration successful!"}
        
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail="Database error: " + str(e))
    finally:
        cursor.close()

@app.post("/login")
def login(data: LoginInfo, db=Depends(get_db)):
    logger.info(f"[/login] Auth attempt for: {data.username}")
    cursor = db.cursor()
    try:
        cursor.execute("SELECT * FROM users WHERE username = ?", (data.username,))
        row = cursor.fetchone()
        
        if not row or not bcrypt.checkpw(data.password.encode("utf-8"), row["password"].encode("utf-8")):
            raise HTTPException(status_code=400, detail="Invalid username or password")

        access_token = create_access_token(data={"sub": data.username})

        user_profile = {
            "username": row["username"],
            "name": row["name"],
            "age": row["age"],
            "gender": row["gender"],
            "height": row["height"],
            "weight": row["weight"],
            "level": row["level"]
        }

        return {
            "message": "Login successful!",
            "token": access_token,
            "user": user_profile
        }
    finally:
        cursor.close()

@app.post("/pta")
def get_pta(data: PTARequest, db=Depends(get_db)):
    cursor = db.cursor()
    exercise_name = data.exercise_name.lower().strip()

    try:
        cursor.execute("""
        SELECT metrics, is_active
        FROM pta
        WHERE username = ? AND exercise_name = ?
        """, (data.user_name, exercise_name))

        row = cursor.fetchone()

        if not row or not row["metrics"] or row["is_active"] == 0:
            return {"pta_metrics": None}

        return {"pta_metrics": json.loads(row["metrics"])}
    finally:
        cursor.close()

@app.post("/save-progress")
def save_progress(data: ProgressData, db=Depends(get_db)):
    logger.info(f"[/save-progress] Saving workout for: {data.user_name} | {data.exercise_name}")
    exercise_name = data.exercise_name.lower().strip()
    cursor = db.cursor()

    try:
        # 1. Save Workout Progress
        cursor.execute("""
        INSERT INTO progress
        (user_name, exercise_name, date_exercised, reps, duration)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            data.user_name,
            exercise_name,
            data.date_exercised.isoformat(),
            data.reps,
            data.duration,
        ))

        # 2. Upsert PTA Metrics (if provided)
        if data.pta_metrics and len(data.pta_metrics) > 0:
            logger.info(f"[/save-progress] Updating dynamically adapted PTA for {data.user_name}")
            cursor.execute("""
            INSERT INTO pta (username, exercise_name, metrics, is_active, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(username, exercise_name)
            DO UPDATE SET
                metrics=excluded.metrics,
                is_active=1,
                updated_at=excluded.updated_at
            """,
            (
                data.user_name,
                exercise_name,
                json.dumps(data.pta_metrics),
                1, 
                datetime.utcnow().isoformat()
            ))

        db.commit()
        return {"success": True}

    except sqlite3.Error as e:
        db.rollback() 
        logger.error(f"[/save-progress] Transaction failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()

@app.get("/get-stats/{user_name}", response_model=StatsResponse)
def get_stats(user_name: str, db=Depends(get_db)):
    cursor = db.cursor()
    try:
        cursor.execute("""
        SELECT COUNT(*) as total_workouts, COALESCE(SUM(reps), 0) as total_reps, COALESCE(SUM(duration), 0) as total_duration
        FROM progress WHERE user_name = ?
        """, (user_name,))
        totals = cursor.fetchone()

        now = datetime.now()
        current_month, current_year = f"{now.month:02d}", str(now.year)

        cursor.execute("""
        SELECT DISTINCT CAST(strftime('%d', date_exercised) AS INTEGER) as day
        FROM progress WHERE user_name = ? AND strftime('%m', date_exercised) = ? AND strftime('%Y', date_exercised) = ?
        """, (user_name, current_month, current_year))
        worked_days = [row["day"] for row in cursor.fetchall()]

        cursor.execute("""
        SELECT exercise_name, date_exercised, reps, duration
        FROM progress WHERE user_name = ? ORDER BY date_exercised DESC LIMIT 1
        """, (user_name,))
        last = cursor.fetchone()

        seven_days_ago = (datetime.utcnow() - timedelta(days=7)).date().isoformat()
        cursor.execute("""
        SELECT COUNT(*) as workouts, COALESCE(SUM(reps), 0) as reps, COALESCE(SUM(duration), 0) as duration
        FROM progress WHERE user_name = ? AND date_exercised >= ?
        """, (user_name, seven_days_ago))
        weekly = cursor.fetchone()

        thirty_days_ago = (datetime.utcnow() - timedelta(days=30)).date().isoformat()
        cursor.execute("""
        SELECT COUNT(*) as workouts, COALESCE(SUM(reps), 0) as reps, COALESCE(SUM(duration), 0) as duration
        FROM progress WHERE user_name = ? AND date_exercised >= ?
        """, (user_name, thirty_days_ago))
        monthly = cursor.fetchone()

        return {
            "totals": {
                "workouts": totals["total_workouts"] or 0,
                "reps": totals["total_reps"] or 0,
                "duration": totals["total_duration"] or 0,
            },
            "weekly": {
                "workouts": weekly["workouts"] or 0,
                "reps": weekly["reps"] or 0,
                "duration": weekly["duration"] or 0,
            },
            "monthly": {
                "workouts": monthly["workouts"] or 0,
                "reps": monthly["reps"] or 0,
                "duration": monthly["duration"] or 0,
            },
            "last_workout": {
                "exercise": last["exercise_name"] if last else None,
                "date": last["date_exercised"] if last else None,
                "reps": last["reps"] if last else 0,
                "duration": last["duration"] if last else 0,
            },
            "worked_days": worked_days
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to fetch stats")
    finally:
        cursor.close()

if __name__ == "__main__":
    logger.info("Starting Trainify Backend server")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)