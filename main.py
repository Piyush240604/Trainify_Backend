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
logger.setLevel(logging.DEBUG)

# Only console handler - no file logging
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)

formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)

logger.addHandler(console_handler)

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
        logger.debug("Users table created/verified")
        
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
        logger.debug("Progress table created/verified")
        conn.commit()
        logger.info("Database initialization complete")
    except Exception as e:
        logger.error(f"Database initialization error | Type: {type(e).__name__} | Error: {e}")
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

@app.on_event("startup")
def on_startup():
    init_db()
    logger.info("Trainify Backend startup complete")


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
            (data.user_name, data.exercise_name),
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
    logger.info(f"[/login] INCOMING REQUEST | Full payload type: {type(data).__name__}")
    logger.info(f"[/login] INCOMING REQUEST | username type: {type(data.username).__name__} | value: {data.username}")
    logger.debug(f"[/login] Password received | type: {type(data.password).__name__} | length: {len(data.password)}")
    
    cursor = db.cursor()
    try:
        logger.debug(f"[/login] Querying user: {data.username}")
        cursor.execute("SELECT * FROM users WHERE username = ?", (data.username,))
        row = cursor.fetchone()
        logger.debug(f"[/login] Query result type: {type(row).__name__} | User found: {row is not None}")
        
        if not row:
            logger.warning(f"[/login] User not found: {data.username}")
            raise HTTPException(status_code=400, detail="Invalid username or password")

        stored_pw = row["password"]
        logger.debug(f"[/login] Retrieved password from DB | type: {type(stored_pw).__name__} | length: {len(stored_pw)}")
        
        password_match = bcrypt.checkpw(data.password.encode("utf-8"), stored_pw.encode("utf-8"))
        logger.debug(f"[/login] Password verification result | type: {type(password_match).__name__} | match: {password_match}")
        
        if not password_match:
            logger.warning(f"[/login] Password mismatch for user: {data.username}")
            raise HTTPException(status_code=400, detail="Invalid username or password")

        logger.info(f"[/login] Login successful for user: {data.username}")
        response = {"message": "Login successful!"}
        logger.info(f"[/login] OUTGOING RESPONSE | Type: {type(response).__name__} | Data: {response}")
        return response
        
    finally:
        cursor.close()

@app.post("/save-progress")
def save_progress(data: ProgressData, db=Depends(get_db)):
    logger.info(f"[/save-progress] INCOMING REQUEST | Full payload type: {type(data).__name__}")
    logger.info(f"[/save-progress] INCOMING REQUEST | user_name type: {type(data.user_name).__name__} | value: {data.user_name}")
    logger.info(f"[/save-progress] INCOMING REQUEST | exercise_name type: {type(data.exercise_name).__name__} | value: {data.exercise_name}")
    logger.info(f"[/save-progress] INCOMING REQUEST | date_exercised type: {type(data.date_exercised).__name__} | value: {data.date_exercised}")
    logger.info(f"[/save-progress] INCOMING REQUEST | reps type: {type(data.reps).__name__} | value: {data.reps}")
    logger.info(f"[/save-progress] INCOMING REQUEST | duration type: {type(data.duration).__name__} | value: {data.duration}")
    logger.debug(f"[/save-progress] INCOMING REQUEST | pta_metrics type: {type(data.pta_metrics).__name__} | value: {data.pta_metrics}")
    
    cursor = db.cursor()
    metrics_json = None
    if data.pta_metrics is not None:
        metrics_json = json.dumps(data.pta_metrics)
        logger.debug(f"[/save-progress] Serialized metrics | type: {type(metrics_json).__name__} | value: {metrics_json}")
    else:
        logger.debug(f"[/save-progress] No pta_metrics to serialize (None)")

    try:
        logger.debug(f"[/save-progress] Inserting progress record into database")
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
        logger.debug(f"[/save-progress] Execute complete | Rows affected: {cursor.rowcount}")
        db.commit()
        logger.info(f"[/save-progress] Progress saved successfully | user: {data.user_name} | exercise: {data.exercise_name}")
        
        response = {"success": True, "message": "Progress saved"}
        logger.info(f"[/save-progress] OUTGOING RESPONSE | Type: {type(response).__name__} | Data: {response}")
        return response
        
    except sqlite3.Error as e:
        logger.error(f"[/save-progress] Database error | Type: {type(e).__name__} | Error: {e}")
        raise HTTPException(status_code=500, detail="Database error: " + str(e))
    finally:
        cursor.close()

@app.get("/")
def read_root():
    logger.info(f"[/] Root endpoint accessed")
    response = {"message": "Welcome to Trainify Backend!"}
    logger.info(f"[/] OUTGOING RESPONSE | Type: {type(response).__name__} | Data: {response}")
    return response

if __name__ == "__main__":
    logger.info("Starting Trainify Backend server")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)