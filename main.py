from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status, Depends
from pydantic import BaseModel, StringConstraints, field_validator
from typing import Annotated, List, Optional, Dict, Any
from fastapi.middleware.cors import CORSMiddleware
from typing import Literal
import sqlite3
import bcrypt
import logging
import uvicorn
import json
from datetime import date, datetime, timedelta, timezone
import jwt

# JWT Configuration (Store this in an environment variable later!)
SECRET_KEY = "Trainify-key"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()   # startup
    yield          # app runs
    
app = FastAPI(lifespan=lifespan)

import logging
import sys

logger = logging.getLogger("trainify")
logger.setLevel(logging.DEBUG)

# Prevent duplicate logs
logger.propagate = False

# Clear existing handlers (important in reload / render)
if logger.hasHandlers():
    logger.handlers.clear()

handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.DEBUG)

formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
handler.setFormatter(formatter)

logger.addHandler(handler)

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
        logger.debug(f"Attempting database connection to: {DB_PATH}")
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        logger.debug(f"Database connection successful | Type: {type(conn).__name__}")
        yield conn
    except sqlite3.Error as e:
        logger.error(f"Database connection error | Type: {type(e).__name__} | Error: {e}")
        raise HTTPException(status_code=500, detail=f"Database connection error: {e}")
    finally:
        if conn:
            logger.debug("Closing database connection")
            conn.close()

def init_db():
    logger.info("Initializing database...")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        # USERS
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

        # PROGRESS
        cur.execute("""
        CREATE TABLE IF NOT EXISTS progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_name TEXT NOT NULL,
            exercise_name TEXT NOT NULL,
            date_exercised TEXT NOT NULL,
            reps INTEGER NOT NULL,
            duration INTEGER NOT NULL,
            pta_metrics TEXT
        )
        """)

        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_exercise_date
        ON progress(user_name, exercise_name, date_exercised)
        """)

        conn.commit()
        logger.info("Database initialization complete")

    except Exception as e:
        logger.error(f"Database init error: {e}")
    finally:
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

class PTARequest(BaseModel):
    user_name: NameStr
    exercise_name: NameStr

@app.post("/pta")
def get_pta(data: PTARequest, db=Depends(get_db)):
    """
    return the stored pta_metrics for the latest entry matching the user/exercise.
    if nothing is found or the field is empty we return `pta_metrics: None`.
    """
    logger.info(f"[/pta] INCOMING REQUEST | user_name type: {type(data.user_name).__name__} | value: {data.user_name}")
    logger.info(f"[/pta] INCOMING REQUEST | exercise_name type: {type(data.exercise_name).__name__} | value: {data.exercise_name}")
    
    cursor = db.cursor()
    exercise_name = data.exercise_name.lower().strip()
    try:
        logger.debug(f"[/pta] Querying progress table for user_name={data.user_name}, exercise_name={data.exercise_name}")
        cursor.execute(
            """
            SELECT pta_metrics
            FROM progress
            WHERE user_name = ? AND exercise_name = ?
            ORDER BY date_exercised DESC
            LIMIT 1
            """,
            (data.user_name, exercise_name),
        )
        row = cursor.fetchone()
        logger.debug(f"[/pta] Query result type: {type(row).__name__} | Result: {row}")
        
        if not row or not row["pta_metrics"]:
            logger.info(f"[/pta] No pta_metrics found for user_name={data.user_name}, exercise_name={data.exercise_name}")
            response = {"pta_metrics": None}
            logger.info(f"[/pta] OUTGOING RESPONSE | Type: {type(response).__name__} | Data: {response}")
            return response

        try:
            metrics = json.loads(row["pta_metrics"])
            logger.debug(f"[/pta] Parsed metrics type: {type(metrics).__name__} | Value: {metrics}")
        except json.JSONDecodeError as e:
            logger.warning(f"[/pta] JSON decode error | Type: {type(e).__name__} | Error: {e}")
            metrics = None

        response = {"pta_metrics": metrics}
        logger.info(f"[/pta] OUTGOING RESPONSE | Type: {type(response).__name__} | Data: {response}")
        return response
    finally:
        cursor.close()

@app.post("/register")
def register(data: SignupData, db=Depends(get_db)):
    logger.info(f"[/register] INCOMING REQUEST | Full payload type: {type(data).__name__}")
    logger.info(f"[/register] INCOMING REQUEST | name type: {type(data.name).__name__} | value: {data.name}")
    logger.info(f"[/register] INCOMING REQUEST | age type: {type(data.age).__name__} | value: {data.age}")
    logger.info(f"[/register] INCOMING REQUEST | gender type: {type(data.gender).__name__} | value: {data.gender}")
    logger.info(f"[/register] INCOMING REQUEST | height type: {type(data.height).__name__} | value: {data.height}")
    logger.info(f"[/register] INCOMING REQUEST | weight type: {type(data.weight).__name__} | value: {data.weight}")
    logger.info(f"[/register] INCOMING REQUEST | username type: {type(data.username).__name__} | value: {data.username}")
    logger.info(f"[/register] INCOMING REQUEST | level type: {type(data.level).__name__} | value: {data.level}")
    logger.debug(f"[/register] Password received | type: {type(data.password).__name__} | length: {len(data.password)}")
    
    cursor = db.cursor()
    gender = data.gender.lower()
    level = data.level.lower()
    logger.debug(f"[/register] Normalized gender: {gender} (type: {type(gender).__name__})")
    logger.debug(f"[/register] Normalized level: {level} (type: {type(level).__name__})")
    
    if gender not in ["male", "female", "other"]:
        logger.warning(f"[/register] Invalid gender: {gender}")
        raise HTTPException(status_code=400, detail="Invalid gender")
    if level not in ["beginner", "intermediate", "advanced"]:
        logger.warning(f"[/register] Invalid level: {level}")
        raise HTTPException(status_code=400, detail="Invalid level")

    try:
        logger.debug(f"[/register] Checking if username exists: {data.username}")
        cursor.execute("SELECT id FROM users WHERE username = ?", (data.username,))
        existing_user = cursor.fetchone()
        logger.debug(f"[/register] Username check result type: {type(existing_user).__name__} | Result: {existing_user}")
        
        if existing_user:
            logger.warning(f"[/register] Username already exists: {data.username}")
            raise HTTPException(status_code=400, detail="Username already exists")

        hashed_pw = bcrypt.hashpw(data.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        logger.debug(f"[/register] Password hashed | type: {type(hashed_pw).__name__} | length: {len(hashed_pw)}")

        logger.debug(f"[/register] Inserting user into database")
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
        logger.info(f"[/register] User inserted successfully | username: {data.username}")
        
        response = {"message": "Registration successful!"}
        logger.info(f"[/register] OUTGOING RESPONSE | Type: {type(response).__name__} | Data: {response}")
        return response
        
    except sqlite3.Error as e:
        logger.error(f"[/register] Database error | Type: {type(e).__name__} | Error: {e}")
        raise HTTPException(status_code=500, detail="Database error: " + str(e))
    finally:
        cursor.close()

@app.post("/login")
def login(data: LoginInfo, db=Depends(get_db)):
    logger.info(f"[/login] INCOMING REQUEST | username: {data.username}")
    
    cursor = db.cursor()
    try:
        cursor.execute("SELECT * FROM users WHERE username = ?", (data.username,))
        row = cursor.fetchone()
        
        if not row:
            raise HTTPException(status_code=400, detail="Invalid username or password")

        stored_pw = row["password"]
        password_match = bcrypt.checkpw(data.password.encode("utf-8"), stored_pw.encode("utf-8"))
        
        if not password_match:
            raise HTTPException(status_code=400, detail="Invalid username or password")

        # 1. Create the JWT Token
        access_token = create_access_token(data={"sub": data.username})

        # 2. Package the safe user data for the mobile app's offline vault
        user_profile = {
            "username": row["username"],
            "name": row["name"],
            "age": row["age"],
            "gender": row["gender"],
            "height": row["height"],
            "weight": row["weight"],
            "level": row["level"]
        }

        # 3. Send the exact structure React Native is expecting
        response = {
            "message": "Login successful!",
            "token": access_token,
            "user": user_profile
        }
        
        logger.info(f"[/login] Login successful for user: {data.username}")
        return response
        
    finally:
        cursor.close()

@app.post("/save-progress")
def save_progress(data: ProgressData, db=Depends(get_db)):

    exercise_name = data.exercise_name.lower().strip()

    cursor = db.cursor()
    metrics_json = json.dumps(data.pta_metrics) if data.pta_metrics else None

    try:
        cursor.execute("""
        INSERT INTO progress
        (user_name, exercise_name, date_exercised, reps, duration, pta_metrics)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            data.user_name,
            exercise_name,
            data.date_exercised.isoformat(),
            data.reps,
            data.duration,
            metrics_json,
        ))

        db.commit()

        return {"success": True}

    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()

# Response Models for stats endpoint
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

@app.get("/get-stats/{user_name}", response_model=StatsResponse)
def get_stats(user_name: str, db=Depends(get_db)):

    logger.info(f"[/get-stats] INCOMING REQUEST | user_name: {user_name}")

    cursor = db.cursor()

    try:
        # --- BASIC TOTALS ---
        logger.debug("[/get-stats] Fetching total stats")
        cursor.execute("""
        SELECT 
            COUNT(*) as total_workouts,
            COALESCE(SUM(reps), 0) as total_reps,
            COALESCE(SUM(duration), 0) as total_duration
        FROM progress
        WHERE user_name = ?
        """, (user_name,))
        
        totals = cursor.fetchone()
        logger.debug(f"[/get-stats] totals: {dict(totals) if totals else None}")

        # --- WORKOUT DAYS THIS MONTH ---
        now = datetime.now()
        current_month = f"{now.month:02d}"
        current_year = str(now.year)

        logger.debug(f"[/get-stats] Fetching worked days | month={current_month}, year={current_year}")

        cursor.execute(
            """
            SELECT DISTINCT CAST(strftime('%d', date_exercised) AS INTEGER) as day
            FROM progress
            WHERE user_name = ?
            AND strftime('%m', date_exercised) = ?
            AND strftime('%Y', date_exercised) = ?
            """,
            (user_name, current_month, current_year)
        )

        rows = cursor.fetchall()
        worked_days = [row["day"] for row in rows] if rows else []
        logger.debug(f"[/get-stats] worked_days: {worked_days}")

        # --- LAST WORKOUT ---
        logger.debug("[/get-stats] Fetching last workout")
        cursor.execute("""
        SELECT exercise_name, date_exercised, reps, duration
        FROM progress
        WHERE user_name = ?
        ORDER BY date_exercised DESC
        LIMIT 1
        """, (user_name,))
        
        last = cursor.fetchone()
        logger.debug(f"[/get-stats] last_workout: {dict(last) if last else None}")

        # --- WEEKLY STATS ---
        seven_days_ago = (datetime.utcnow() - timedelta(days=7)).date().isoformat()
        logger.debug(f"[/get-stats] Fetching weekly stats since {seven_days_ago}")

        cursor.execute("""
        SELECT 
            COUNT(*) as workouts,
            COALESCE(SUM(reps), 0) as reps,
            COALESCE(SUM(duration), 0) as duration
        FROM progress
        WHERE user_name = ? AND date_exercised >= ?
        """, (user_name, seven_days_ago))

        weekly = cursor.fetchone()
        logger.debug(f"[/get-stats] weekly: {dict(weekly) if weekly else None}")

        # --- MONTHLY STATS ---
        thirty_days_ago = (datetime.utcnow() - timedelta(days=30)).date().isoformat()
        logger.debug(f"[/get-stats] Fetching monthly stats since {thirty_days_ago}")

        cursor.execute("""
        SELECT 
            COUNT(*) as workouts,
            COALESCE(SUM(reps), 0) as reps,
            COALESCE(SUM(duration), 0) as duration
        FROM progress
        WHERE user_name = ? AND date_exercised >= ?
        """, (user_name, thirty_days_ago))

        monthly = cursor.fetchone()
        logger.debug(f"[/get-stats] monthly: {dict(monthly) if monthly else None}")

        # --- RESPONSE ---
        response = {
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

        logger.info(f"[/get-stats] SUCCESS RESPONSE | user={user_name} | data={response}")

        return response

    except Exception as e:
        logger.error(f"[/get-stats] ERROR | Type: {type(e).__name__} | Error: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch stats")

    finally:
        logger.debug("[/get-stats] Closing cursor")
        cursor.close()

@app.get("/")
def read_root():
    logger.info(f"[/] Root endpoint accessed")
    response = {"message": "Welcome to Trainify Backend!"}
    logger.info(f"[/] OUTGOING RESPONSE | Type: {type(response).__name__} | Data: {response}")
    return response

if __name__ == "__main__":
    logger.info("Starting Trainify Backend server")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, log_level="debug")