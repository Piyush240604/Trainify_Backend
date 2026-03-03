from fastapi import FastAPI, HTTPException, status, Depends
from pydantic import BaseModel, StringConstraints, constr, field_validator
import bcrypt
import mysql.connector
import logging
from mysql.connector import Error
from typing import Annotated, Optional
from fastapi.middleware.cors import CORSMiddleware
from typing import Literal

app = FastAPI()

logger = logging.getLogger("trainify")          # or root logger
logger.setLevel(logging.INFO) 

# Allow CORS for frontend dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust for production!
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# MySQL connection config
def get_db():
    try:
        conn = mysql.connector.connect(
            host="localhost",
            user="root",           # Change as needed
            password="root",           # Change as needed
            database="trainify",
        )
        yield conn
    finally:
        if conn.is_connected():
            conn.close()

NameStr = Annotated[str, StringConstraints(max_length=100)]
UsernameStr = Annotated[str, StringConstraints(max_length=50)]
LowerStr = Annotated[str, StringConstraints(to_lower=True)]

# Pydantic models
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
        # allow digit strings from the frontend
        if isinstance(v, str) and v.isdigit():
            return int(v)
        return v

class LoginInfo(BaseModel):
    username: UsernameStr
    password: str

@app.post("/register")
def register(data: SignupData, db=Depends(get_db)):

    logger.info(f"Attempting registration: {data}")

    cursor = db.cursor(dictionary=True)
    # Ensure gender and level are lowercase
    gender = data.gender.lower()
    level = data.level.lower()
    if gender not in ["male", "female", "other"]:
        raise HTTPException(status_code=400, detail="Invalid gender")
    if level not in ["beginner", "intermediate", "advanced"]:
        raise HTTPException(status_code=400, detail="Invalid level")

    # Check if username exists
    cursor.execute("SELECT id FROM users WHERE username = %s", (data.username,))
    if cursor.fetchone():
        raise HTTPException(status_code=400, detail="Username already exists")

    # Hash password
    hashed_pw = bcrypt.hashpw(data.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    # Insert user
    try:
        cursor.execute(
            """
            INSERT INTO users (name, age, gender, height, weight, username, password, level)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
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
    except Error as e:
        raise HTTPException(status_code=500, detail="Database error: " + str(e))
    finally:
        cursor.close()

    return {"message": "Registration successful!"}

@app.post("/login")
def login(data: LoginInfo, db=Depends(get_db)):
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE username = %s", (data.username,))
    user = cursor.fetchone()
    cursor.close()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")

    if not bcrypt.checkpw(data.password.encode("utf-8"), user["password"].encode("utf-8")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")

    return {"message": "Login successful!"}

@app.get("/")
def read_root():
    return {"message": "Welcome to Trainify Backend!"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)