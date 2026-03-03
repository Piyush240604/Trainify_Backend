from fastapi import FastAPI, HTTPException, status, Depends
from pydantic import BaseModel, StringConstraints, field_validator
from typing import Annotated
from fastapi.middleware.cors import CORSMiddleware
from typing import Literal
import sqlite3
import bcrypt
import logging
import uvicorn

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

@app.on_event("startup")
def on_startup():
    init_db()
    logger.info("Initialized sqlite DB")

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

@app.get("/")
def read_root():
    return {"message": "Welcome to Trainify Backend!"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)