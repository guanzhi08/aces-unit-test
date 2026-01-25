"""
FastAPI backend for ACES Unit Test Practice
- Records exam results with username in SQLite/PostgreSQL database
- Provides score history and curve data
- Uses SQLite locally, PostgreSQL on render.com (via DATABASE_URL)
"""

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timezone, timedelta
import os
import hashlib
import secrets
import json
import io

# Database configuration
DATABASE_URL = os.environ.get("DATABASE_URL")

# Determine which database to use
if DATABASE_URL:
    # PostgreSQL on render.com
    import psycopg2
    from psycopg2.extras import RealDictCursor
    USE_POSTGRES = True
    # Fix for render.com: postgres:// -> postgresql://
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    print(f"Using PostgreSQL database")
else:
    # SQLite for local development
    import sqlite3
    USE_POSTGRES = False
    DB_PATH = "exam_results.db"
    print(f"Using SQLite database: {DB_PATH}")

app = FastAPI(title="ACES Unit Test API")

# Enable CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db_connection():
    """Create a database connection."""
    if USE_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        return conn
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn


def sql(query):
    """Convert SQLite-style ? placeholders to PostgreSQL %s if needed."""
    if USE_POSTGRES:
        return query.replace('?', '%s')
    return query


def get_last_insert_id(cursor, conn):
    """Get the last inserted ID (different for SQLite vs PostgreSQL)."""
    if USE_POSTGRES:
        cursor.execute("SELECT lastval()")
        return cursor.fetchone()['lastval']
    else:
        return cursor.lastrowid


def format_exam_date(exam_date):
    """Format exam_date to string consistently."""
    if exam_date is None:
        return ""
    # Target timezone: UTC+8
    target_tz = timezone(timedelta(hours=8))

    # If value is a string, try to parse it first
    if isinstance(exam_date, str):
        # Try ISO first, then common formats; fall back to trimming
        dt = None
        try:
            dt = datetime.fromisoformat(exam_date)
        except Exception:
            for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
                try:
                    dt = datetime.strptime(exam_date, fmt)
                    break
                except Exception:
                    dt = None
        if dt is None:
            return exam_date[:16] if len(exam_date) >= 16 else exam_date
        # Assume naive datetimes are UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(target_tz).strftime('%Y-%m-%d %H:%M')

    # For datetime objects (PostgreSQL returns datetime)
    if isinstance(exam_date, datetime):
        dt = exam_date
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(target_tz).strftime('%Y-%m-%d %H:%M')

    return str(exam_date)


def init_db():
    """Initialize the database with required tables."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if USE_POSTGRES:
        # PostgreSQL syntax
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS exam_results (
                id SERIAL PRIMARY KEY,
                username TEXT NOT NULL,
                unit_number TEXT NOT NULL,
                score REAL NOT NULL,
                type_accuracy REAL NOT NULL,
                correct_count INTEGER NOT NULL,
                total_questions INTEGER NOT NULL,
                exam_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS admin_settings (
                id SERIAL PRIMARY KEY,
                setting_key TEXT UNIQUE NOT NULL,
                setting_value TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS admin_sessions (
                token TEXT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS incorrect_answers (
                id SERIAL PRIMARY KEY,
                username TEXT,
                unit_number TEXT NOT NULL,
                chinese TEXT NOT NULL,
                english TEXT NOT NULL,
                last_incorrect TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Users table for account creation
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
    else:
        # SQLite syntax
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS exam_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                unit_number TEXT NOT NULL,
                score REAL NOT NULL,
                type_accuracy REAL NOT NULL,
                correct_count INTEGER NOT NULL,
                total_questions INTEGER NOT NULL,
                exam_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Users table for account creation
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS admin_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                setting_key TEXT UNIQUE NOT NULL,
                setting_value TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS admin_sessions (
                token TEXT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS incorrect_answers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT,
                unit_number TEXT NOT NULL,
                chinese TEXT NOT NULL,
                english TEXT NOT NULL,
                last_incorrect TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
    
    # Set default admin password if not exists (default: 'admin123')
    default_password_hash = hashlib.sha256('admin123'.encode()).hexdigest()
    try:
        if USE_POSTGRES:
            cursor.execute('''
                INSERT INTO admin_settings (setting_key, setting_value)
                VALUES ('admin_password', %s)
                ON CONFLICT (setting_key) DO NOTHING
            ''', (default_password_hash,))
        else:
            cursor.execute('''
                INSERT OR IGNORE INTO admin_settings (setting_key, setting_value)
                VALUES ('admin_password', ?)
            ''', (default_password_hash,))
    except Exception:
        pass  # Already exists
    
    conn.commit()
    conn.close()


# Initialize database on startup
init_db()


# Pydantic models for request/response
class ExamResult(BaseModel):
    username: str
    unit_number: str
    score: float
    type_accuracy: float
    correct_count: int
    total_questions: int


class ExamResultResponse(BaseModel):
    id: int
    username: str
    unit_number: str
    score: float
    type_accuracy: float
    correct_count: int
    total_questions: int
    exam_date: str


class ScoreCurveData(BaseModel):
    dates: List[str]
    scores: List[float]
    type_accuracies: List[float]


class IncorrectItem(BaseModel):
    username: Optional[str] = None
    unit_number: str
    chinese: str
    english: str


class UserStats(BaseModel):
    username: str
    total_exams: int
    average_score: float
    average_type_accuracy: float
    best_score: float
    recent_scores: List[float]


class ExamResultUpdate(BaseModel):
    unit_number: Optional[str] = None
    score: Optional[float] = None
    type_accuracy: Optional[float] = None
    correct_count: Optional[int] = None
    total_questions: Optional[int] = None


class IncorrectItemUpdate(BaseModel):
    chinese: Optional[str] = None
    english: Optional[str] = None
    unit_number: Optional[str] = None


@app.post("/api/results", response_model=ExamResultResponse)
async def save_exam_result(result: ExamResult):
    """Save an exam result to the database."""
    if not result.username.strip():
        raise HTTPException(status_code=400, detail="Username is required")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute(sql('''
        INSERT INTO exam_results (username, unit_number, score, type_accuracy, correct_count, total_questions)
        VALUES (?, ?, ?, ?, ?, ?)
    '''), (result.username.strip(), result.unit_number, result.score, result.type_accuracy, 
          result.correct_count, result.total_questions))
    
    conn.commit()
    result_id = get_last_insert_id(cursor, conn)
    
    # Fetch the inserted record
    cursor.execute(sql('SELECT * FROM exam_results WHERE id = ?'), (result_id,))
    row = cursor.fetchone()
    conn.close()
    
    return ExamResultResponse(
        id=row['id'],
        username=row['username'],
        unit_number=row['unit_number'],
        score=row['score'],
        type_accuracy=row['type_accuracy'],
        correct_count=row['correct_count'],
        total_questions=row['total_questions'],
        exam_date=format_exam_date(row['exam_date'])
    )


@app.post('/api/incorrect')
async def save_incorrect(items: List[IncorrectItem]):
    """Save incorrect answers (accepts a list of incorrect items)."""
    if not items:
        return {"saved": 0}

    conn = get_db_connection()
    cursor = conn.cursor()
    saved = 0
    for it in items:
        try:
            cursor.execute(sql('''
                INSERT INTO incorrect_answers (username, unit_number, chinese, english)
                VALUES (?, ?, ?, ?)
            '''), (it.username or '', it.unit_number, it.chinese, it.english))
            saved += 1
        except Exception:
            continue

    conn.commit()
    conn.close()
    return {"saved": saved}


@app.get('/api/incorrect')
async def get_incorrect(unit: Optional[str] = None, username: Optional[str] = None, limit: int = 1000):
    """Return incorrect answers for a given unit (optionally filtered by username)."""
    conn = get_db_connection()
    cursor = conn.cursor()

    params = []
    if unit and username:
        cursor.execute(sql('''
            SELECT * FROM incorrect_answers WHERE unit_number = ? AND username = ? ORDER BY last_incorrect DESC LIMIT ?
        '''), (unit, username, limit))
    elif unit:
        cursor.execute(sql('''
            SELECT * FROM incorrect_answers WHERE unit_number = ? ORDER BY last_incorrect DESC LIMIT ?
        '''), (unit, limit))
    elif username:
        cursor.execute(sql('''
            SELECT * FROM incorrect_answers WHERE username = ? ORDER BY last_incorrect DESC LIMIT ?
        '''), (username, limit))
    else:
        cursor.execute(sql('SELECT * FROM incorrect_answers ORDER BY last_incorrect DESC LIMIT ?'), (limit,))

    rows = cursor.fetchall()
    conn.close()

    # Normalize output keys to expected casing (English/Chinese)
    result = []
    for row in rows:
        result.append({
            'id': row['id'],
            'username': row.get('username', row['username']) if isinstance(row, dict) else row['username'],
            'unit_number': row['unit_number'],
            'Chinese': row['chinese'],
            'English': row['english'],
            'last_incorrect': format_exam_date(row['last_incorrect'])
        })
    return result


@app.put('/api/incorrect/{item_id}')
async def update_incorrect(item_id: int, update: IncorrectItemUpdate):
    """Update an incorrect answer record."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if record exists
    cursor.execute(sql('SELECT * FROM incorrect_answers WHERE id = ?'), (item_id,))
    existing = cursor.fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=404, detail="Record not found")
    
    # Build update query dynamically
    updates = []
    values = []
    if update.chinese is not None:
        updates.append('chinese = ?')
        values.append(update.chinese)
    if update.english is not None:
        updates.append('english = ?')
        values.append(update.english)
    if update.unit_number is not None:
        updates.append('unit_number = ?')
        values.append(update.unit_number)
    
    if updates:
        values.append(item_id)
        query = f'UPDATE incorrect_answers SET {", ".join(updates)} WHERE id = ?'
        cursor.execute(sql(query), tuple(values))
        conn.commit()
    
    # Fetch updated record
    cursor.execute(sql('SELECT * FROM incorrect_answers WHERE id = ?'), (item_id,))
    row = cursor.fetchone()
    conn.close()
    
    return {
        'id': row['id'],
        'username': row['username'],
        'unit_number': row['unit_number'],
        'Chinese': row['chinese'],
        'English': row['english'],
        'last_incorrect': format_exam_date(row['last_incorrect'])
    }


@app.delete('/api/incorrect/{item_id}')
async def delete_incorrect(item_id: int):
    """Delete an incorrect answer record."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if record exists
    cursor.execute(sql('SELECT id FROM incorrect_answers WHERE id = ?'), (item_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Record not found")
    
    cursor.execute(sql('DELETE FROM incorrect_answers WHERE id = ?'), (item_id,))
    conn.commit()
    conn.close()
    
    return {"message": "Record deleted successfully", "id": item_id}


@app.get("/api/results/{username}", response_model=List[ExamResultResponse])
async def get_user_results(username: str, limit: int = 50):
    """Get exam results for a specific user."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute(sql('''
        SELECT * FROM exam_results 
        WHERE username = ? 
        ORDER BY exam_date DESC 
        LIMIT ?
    '''), (username, limit))
    
    rows = cursor.fetchall()
    conn.close()
    
    return [ExamResultResponse(
        id=row['id'],
        username=row['username'],
        unit_number=row['unit_number'],
        score=row['score'],
        type_accuracy=row['type_accuracy'],
        correct_count=row['correct_count'],
        total_questions=row['total_questions'],
        exam_date=format_exam_date(row['exam_date'])
    ) for row in rows]


@app.get("/api/all-results", response_model=List[ExamResultResponse])
async def get_all_results(limit: int = 500):
    """Get all exam results (for admin view)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute(sql('''
        SELECT * FROM exam_results 
        ORDER BY exam_date DESC 
        LIMIT ?
    '''), (limit,))
    
    rows = cursor.fetchall()
    conn.close()
    
    return [ExamResultResponse(
        id=row['id'],
        username=row['username'],
        unit_number=row['unit_number'],
        score=row['score'],
        type_accuracy=row['type_accuracy'],
        correct_count=row['correct_count'],
        total_questions=row['total_questions'],
        exam_date=format_exam_date(row['exam_date'])
    ) for row in rows]


@app.put("/api/results/{result_id}", response_model=ExamResultResponse)
async def update_exam_result(result_id: int, update: ExamResultUpdate):
    """Update an exam result."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if record exists
    cursor.execute(sql('SELECT * FROM exam_results WHERE id = ?'), (result_id,))
    existing = cursor.fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=404, detail="Record not found")
    
    # Build update query dynamically
    updates = []
    values = []
    if update.unit_number is not None:
        updates.append('unit_number = ?')
        values.append(update.unit_number)
    if update.score is not None:
        updates.append('score = ?')
        values.append(update.score)
    if update.type_accuracy is not None:
        updates.append('type_accuracy = ?')
        values.append(update.type_accuracy)
    if update.correct_count is not None:
        updates.append('correct_count = ?')
        values.append(update.correct_count)
    if update.total_questions is not None:
        updates.append('total_questions = ?')
        values.append(update.total_questions)
    
    if updates:
        values.append(result_id)
        query = f'UPDATE exam_results SET {", ".join(updates)} WHERE id = ?'
        cursor.execute(sql(query), tuple(values))
        conn.commit()
    
    # Fetch updated record
    cursor.execute(sql('SELECT * FROM exam_results WHERE id = ?'), (result_id,))
    row = cursor.fetchone()
    conn.close()
    
    return ExamResultResponse(
        id=row['id'],
        username=row['username'],
        unit_number=row['unit_number'],
        score=row['score'],
        type_accuracy=row['type_accuracy'],
        correct_count=row['correct_count'],
        total_questions=row['total_questions'],
        exam_date=format_exam_date(row['exam_date'])
    )


@app.delete("/api/results/{result_id}")
async def delete_exam_result(result_id: int):
    """Delete an exam result."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if record exists
    cursor.execute(sql('SELECT id FROM exam_results WHERE id = ?'), (result_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Record not found")
    
    cursor.execute(sql('DELETE FROM exam_results WHERE id = ?'), (result_id,))
    conn.commit()
    conn.close()
    
    return {"message": "Record deleted successfully", "id": result_id}


@app.get("/api/curve/{username}", response_model=ScoreCurveData)
async def get_score_curve(username: str, unit: Optional[str] = None, limit: int = 20):
    """Get score curve data for a specific user (last N exams), optionally filtered by unit."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if unit:
        cursor.execute(sql('''
            SELECT exam_date, score, type_accuracy 
            FROM exam_results 
            WHERE username = ? AND unit_number = ?
            ORDER BY exam_date ASC 
            LIMIT ?
        '''), (username, unit, limit))
    else:
        cursor.execute(sql('''
            SELECT exam_date, score, type_accuracy 
            FROM exam_results 
            WHERE username = ? 
            ORDER BY exam_date ASC 
            LIMIT ?
        '''), (username, limit))
    
    rows = cursor.fetchall()
    conn.close()
    
    dates = [format_exam_date(row['exam_date']) for row in rows]
    scores = [row['score'] for row in rows]
    type_accuracies = [row['type_accuracy'] for row in rows]
    
    return ScoreCurveData(dates=dates, scores=scores, type_accuracies=type_accuracies)


@app.get("/api/curve-by-count/{username}")
async def get_score_curve_by_count(username: str, unit: Optional[str] = None):
    """Get score curve data grouped by question count (5, 10, ALL) for a specific user."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Define question count categories
    categories = {
        '5': (5, 5),
        '10': (10, 10),
        'ALL': (11, 1000)  # Anything more than 10 is considered "ALL"
    }
    
    result = {}
    for cat_name, (min_q, max_q) in categories.items():
        if unit:
            cursor.execute(sql('''
                SELECT exam_date, score, type_accuracy 
                FROM exam_results 
                WHERE username = ? AND unit_number = ? AND total_questions >= ? AND total_questions <= ?
                ORDER BY exam_date ASC
            '''), (username, unit, min_q, max_q))
        else:
            cursor.execute(sql('''
                SELECT exam_date, score, type_accuracy 
                FROM exam_results 
                WHERE username = ? AND total_questions >= ? AND total_questions <= ?
                ORDER BY exam_date ASC
            '''), (username, min_q, max_q))
        
        rows = cursor.fetchall()
        result[cat_name] = {
            'dates': [format_exam_date(row['exam_date']) for row in rows],
            'scores': [row['score'] for row in rows],
            'type_accuracies': [row['type_accuracy'] for row in rows]
        }
    
    conn.close()
    return result


@app.get("/api/curve-by-unit/{username}")
async def get_score_curve_by_unit(username: str):
    """Get score curve data grouped by unit for a specific user."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get all distinct units for this user
    cursor.execute(sql('''
        SELECT DISTINCT unit_number FROM exam_results 
        WHERE username = ? 
        ORDER BY CAST(unit_number AS INTEGER)
    '''), (username,))
    units = [row['unit_number'] for row in cursor.fetchall()]
    
    # Get data for each unit
    result = {}
    for unit in units:
        cursor.execute(sql('''
            SELECT exam_date, score, type_accuracy 
            FROM exam_results 
            WHERE username = ? AND unit_number = ?
            ORDER BY exam_date ASC
        '''), (username, unit))
        rows = cursor.fetchall()
        result[unit] = {
            'dates': [format_exam_date(row['exam_date']) for row in rows],
            'scores': [row['score'] for row in rows],
            'type_accuracies': [row['type_accuracy'] for row in rows]
        }
    
    conn.close()
    return {'units': units, 'data': result}


@app.get("/api/stats/{username}", response_model=UserStats)
async def get_user_stats(username: str, unit: Optional[str] = None):
    """Get statistics for a specific user, optionally filtered by unit."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if unit:
        cursor.execute(sql('''
            SELECT 
                COUNT(*) as total_exams,
                AVG(score) as avg_score,
                AVG(type_accuracy) as avg_type_accuracy,
                MAX(score) as best_score
            FROM exam_results 
            WHERE username = ? AND unit_number = ?
        '''), (username, unit))
    else:
        cursor.execute(sql('''
            SELECT 
                COUNT(*) as total_exams,
                AVG(score) as avg_score,
                AVG(type_accuracy) as avg_type_accuracy,
                MAX(score) as best_score
            FROM exam_results 
            WHERE username = ?
        '''), (username,))
    
    stats = cursor.fetchone()
    
    # Get recent scores for mini chart
    if unit:
        cursor.execute(sql('''
            SELECT score FROM exam_results 
            WHERE username = ? AND unit_number = ?
            ORDER BY exam_date DESC 
            LIMIT 10
        '''), (username, unit))
    else:
        cursor.execute(sql('''
            SELECT score FROM exam_results 
            WHERE username = ? 
            ORDER BY exam_date DESC 
            LIMIT 10
        '''), (username,))
    
    recent = cursor.fetchall()
    conn.close()
    
    if stats['total_exams'] == 0:
        return UserStats(
            username=username,
            total_exams=0,
            average_score=0,
            average_type_accuracy=0,
            best_score=0,
            recent_scores=[]
        )
    
    return UserStats(
        username=username,
        total_exams=stats['total_exams'],
        average_score=round(stats['avg_score'], 1),
        average_type_accuracy=round(stats['avg_type_accuracy'], 1),
        best_score=stats['best_score'],
        recent_scores=[row['score'] for row in reversed(recent)]
    )


@app.get("/api/users")
async def get_all_users():
    """Get list of all users who have taken exams."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT DISTINCT username, COUNT(*) as exam_count 
        FROM exam_results 
        GROUP BY username 
        ORDER BY username
    ''')
    
    rows = cursor.fetchall()
    conn.close()
    
    return [{"username": row['username'], "exam_count": row['exam_count']} for row in rows]


# Serve static files (index.html, CSV, etc.)
@app.get("/")
async def read_root():
    """Serve the main HTML page."""
    return FileResponse("index.html")


from fastapi.responses import HTMLResponse


class AdminLogin(BaseModel):
    password: str


class AdminPasswordChange(BaseModel):
    old_password: str
    new_password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


def verify_admin_token(token: str) -> bool:
    """Verify if admin session token is valid."""
    if not token:
        return False
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(sql('SELECT token FROM admin_sessions WHERE token = ?'), (token,))
    result = cursor.fetchone()
    conn.close()
    return result is not None


@app.post("/api/users/create")
async def create_user(data: CreateUserRequest):
    """Create a new user with username and password."""
    username = data.username.strip()
    password = data.password
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required")

    password_hash = hashlib.sha256(password.encode()).hexdigest()

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(sql('INSERT INTO users (username, password_hash) VALUES (?, ?)'), (username, password_hash))
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail="Username already exists or invalid")

    conn.close()
    return {"success": True, "username": username}


@app.post("/api/users/login")
async def user_login(data: LoginRequest):
    """Verify a user's username and password."""
    username = data.username.strip()
    password = data.password
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required")

    password_hash = hashlib.sha256(password.encode()).hexdigest()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(sql('SELECT id, username, password_hash FROM users WHERE username = ?'), (username,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if row['password_hash'] != password_hash:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Return basic user info (no session token yet)
    return {"success": True, "username": username}


@app.get("/create-user")
async def create_user_page():
    """Serve a simple HTML page to create user accounts."""
    html = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Create Account - ACES</title>
    <style>
        body { font-family: Arial, sans-serif; display:flex; justify-content:center; align-items:center; min-height:100vh; margin:0; background:#f7f7f7; }
        .card { background:white; padding:24px; border-radius:8px; box-shadow:0 4px 12px rgba(0,0,0,0.08); width:320px; }
        input { width:100%; padding:10px; margin:8px 0; border:1px solid #ddd; border-radius:6px; }
        button { width:100%; padding:10px; background:#2196F3; color:white; border:none; border-radius:6px; cursor:pointer; }
        .error { color:#f44336; display:none; }
        .success { color:#4caf50; display:none; }
    </style>
</head>
<body>
    <div class="card">
        <h2>Create Account</h2>
        <form onsubmit="return submitForm(event)">
            <input id="username" placeholder="Username" required />
            <input id="password" type="password" placeholder="Password" required />
            <button type="submit">Create</button>
        </form>
        <p class="error" id="error"></p>
        <p class="success" id="success">Account created successfully. You may close this page.</p>
    </div>
    <script>
        async function submitForm(e) {
            e.preventDefault();
            const username = document.getElementById('username').value.trim();
            const password = document.getElementById('password').value;
            const errorEl = document.getElementById('error');
            const successEl = document.getElementById('success');
            errorEl.style.display = 'none';
            successEl.style.display = 'none';
            try {
                const resp = await fetch('/api/users/create', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username, password })
                });
                if (resp.ok) {
                    successEl.style.display = 'block';
                } else {
                    const json = await resp.json();
                    errorEl.textContent = json.detail || 'Failed to create account';
                    errorEl.style.display = 'block';
                }
            } catch (err) {
                errorEl.textContent = 'Network error';
                errorEl.style.display = 'block';
            }
            return false;
        }
    </script>
</body>
</html>
'''
    return HTMLResponse(content=html)


@app.post("/api/admin/login")
async def admin_login(login: AdminLogin):
    """Verify admin password and return session token."""
    password_hash = hashlib.sha256(login.password.encode()).hexdigest()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(sql("SELECT setting_value FROM admin_settings WHERE setting_key = 'admin_password'"))
    result = cursor.fetchone()
    
    if not result or result['setting_value'] != password_hash:
        conn.close()
        raise HTTPException(status_code=401, detail="Invalid password")
    
    # Generate session token
    token = secrets.token_urlsafe(32)
    cursor.execute(sql('INSERT INTO admin_sessions (token) VALUES (?)'), (token,))
    conn.commit()
    conn.close()
    
    return {"success": True, "token": token}


@app.post("/api/admin/verify")
async def admin_verify(token: str = ""):
    """Verify if session token is valid."""
    if verify_admin_token(token):
        return {"valid": True}
    raise HTTPException(status_code=401, detail="Invalid or expired token")


@app.post("/api/admin/logout")
async def admin_logout(token: str = ""):
    """Invalidate admin session token."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(sql('DELETE FROM admin_sessions WHERE token = ?'), (token,))
    conn.commit()
    conn.close()
    return {"success": True}


@app.post("/api/admin/change-password")
async def admin_change_password(data: AdminPasswordChange, token: str = ""):
    """Change admin password."""
    if not verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    old_hash = hashlib.sha256(data.old_password.encode()).hexdigest()
    new_hash = hashlib.sha256(data.new_password.encode()).hexdigest()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(sql("SELECT setting_value FROM admin_settings WHERE setting_key = 'admin_password'"))
    result = cursor.fetchone()
    
    if not result or result['setting_value'] != old_hash:
        conn.close()
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    
    cursor.execute(sql("UPDATE admin_settings SET setting_value = ? WHERE setting_key = 'admin_password'"), (new_hash,))
    conn.commit()
    conn.close()
    
    return {"success": True, "message": "Password changed successfully"}


@app.get("/admin")
async def read_admin():
    """Serve the admin login page."""
    login_html = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin Login - ACES</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
            background-color: #f5f5f5;
        }
        .login-container {
            background: white;
            padding: 40px;
            border-radius: 10px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            text-align: center;
            max-width: 400px;
            width: 90%;
        }
        h2 { color: #333; margin-bottom: 30px; }
        input[type="password"] {
            width: 100%;
            padding: 12px;
            margin: 10px 0 20px;
            border: 2px solid #ddd;
            border-radius: 5px;
            font-size: 16px;
            box-sizing: border-box;
        }
        input[type="password"]:focus {
            outline: none;
            border-color: #9C27B0;
        }
        button {
            background-color: #9C27B0;
            color: white;
            padding: 12px 40px;
            border: none;
            border-radius: 5px;
            font-size: 16px;
            cursor: pointer;
            width: 100%;
        }
        button:hover { background-color: #7B1FA2; }
        .error {
            color: #f44336;
            margin-top: 15px;
            display: none;
        }
    </style>
</head>
<body>
    <div class="login-container">
        <h2>🔐 Admin Login</h2>
        <form onsubmit="return handleLogin(event)">
            <input type="password" id="password" placeholder="Enter admin password" required>
            <button type="submit">Login</button>
        </form>
        <p class="error" id="error">Invalid password</p>
    </div>
    <script>
        // Check if already logged in
        const token = localStorage.getItem('adminToken');
        if (token) {
            verifyAndRedirect(token);
        }
        
        async function verifyAndRedirect(token) {
            try {
                const resp = await fetch('/api/admin/verify?token=' + encodeURIComponent(token), { method: 'POST' });
                if (resp.ok) {
                    window.location.href = '/admin/dashboard';
                } else {
                    localStorage.removeItem('adminToken');
                }
            } catch (e) {
                localStorage.removeItem('adminToken');
            }
        }
        
        async function handleLogin(e) {
            e.preventDefault();
            const password = document.getElementById('password').value;
            const errorEl = document.getElementById('error');
            errorEl.style.display = 'none';
            
            try {
                const resp = await fetch('/api/admin/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ password: password })
                });
                
                if (resp.ok) {
                    const data = await resp.json();
                    localStorage.setItem('adminToken', data.token);
                    window.location.href = '/admin/dashboard';
                } else {
                    errorEl.style.display = 'block';
                }
            } catch (err) {
                errorEl.textContent = 'Login failed. Please try again.';
                errorEl.style.display = 'block';
            }
            return false;
        }
    </script>
</body>
</html>
'''
    return HTMLResponse(content=login_html)


@app.get("/admin/dashboard")
async def read_admin_dashboard():
    """Serve the admin dashboard page with records management button."""
    with open("index.html", "r", encoding="utf-8") as f:
        html_content = f.read()
    
    # Inject admin button and logout functionality
        admin_button = '''<button id="recordsBtn" style="background-color: #9C27B0;">考試紀錄</button>
            <button id="incorrectMgmtBtn" style="background-color: #FF9800;">錯字管理</button>
            <button id="userMgmtBtn" style="background-color: #2196F3;">使用者管理</button>
            <button onclick="exportDatabase()" style="background-color: #607D8B;">匯出資料庫</button>
            <button onclick="document.getElementById('importFileInput').click()" style="background-color: #795548;">匯入資料庫</button>
            <input type="file" id="importFileInput" accept=".json" style="display:none;" onchange="importDatabase(this)">
            <button onclick="adminLogout()" style="background-color: #f44336;">登出</button>'''
    html_content = html_content.replace("<!-- ADMIN_BUTTON_PLACEHOLDER -->", admin_button)
    
    # Inject admin verification script
    admin_script = '''
    <script>
        // Verify admin token on page load
        (async function() {
            const token = localStorage.getItem('adminToken');
            if (!token) {
                window.location.href = '/admin';
                return;
            }
            try {
                const resp = await fetch('/api/admin/verify?token=' + encodeURIComponent(token), { method: 'POST' });
                if (!resp.ok) {
                    localStorage.removeItem('adminToken');
                    window.location.href = '/admin';
                } else {
                    // Hide non-admin controls when viewing as admin
                    const hideIds = ['unitNumber','questionCount','showTableBtn','startBtn','historyBtn','resetBtn','incorrectExamBtn'];
                    hideIds.forEach(id => {
                        try {
                            const el = document.getElementById(id);
                            if (el) el.style.display = 'none';
                        } catch (e) {}
                    });
                    // Also hide normal user login inputs/buttons so the admin view doesn't show them
                    const hideLoginIds = ['loginControls','loginBtn','createAcctBtn','loginPassword','username','logoutBtn','createAccountArea','loginStatus','userStats', 'labelUnit', 'labelQuestionCount'];
                    hideLoginIds.forEach(id => {
                        try {
                            const el = document.getElementById(id);
                            if (el) el.style.display = 'none';
                        } catch (e) {}
                    });

                    // Ensure exam area is visible for admin (override guest UI hiding)
                    try {
                        const examAreaEl = document.getElementById('examArea');
                        if (examAreaEl) examAreaEl.style.display = '';
                    } catch (e) {}

                    // Ensure ranking controls (Unit selector + ranking button) are visible for admin
                    try {
                        const showIds = ['labelUnit','unitNumber','rankingBtn'];
                        showIds.forEach(id => {
                            try {
                                const el = document.getElementById(id);
                                if (!el) return;
                                if (el.tagName === 'SELECT' || el.tagName === 'DIV') el.style.display = '';
                                else el.style.display = 'inline-block';
                            } catch (e) {}
                        });
                    } catch (e) {}

                    // Attach explicit handlers to injected admin buttons to ensure they call the page functions
                    try {
                        const rBtn = document.getElementById('recordsBtn');
                        if (rBtn) {
                            rBtn.addEventListener('click', function(e) {
                                e.preventDefault();
                                const callRecords = () => {
                                    if (typeof window.showExamRecords === 'function') {
                                        window.showExamRecords();
                                    } else {
                                        // Retry shortly in case scripts haven't initialized yet
                                        setTimeout(() => {
                                            if (typeof window.showExamRecords === 'function') window.showExamRecords();
                                            else console.error('showExamRecords not found');
                                        }, 200);
                                    }
                                };
                                callRecords();
                            });
                        }
                        const uBtn = document.getElementById('userMgmtBtn');
                        if (uBtn) {
                            uBtn.addEventListener('click', function(e) {
                                e.preventDefault();
                                const callUserMgmt = () => {
                                    if (typeof window.showUserManagement === 'function') {
                                        window.showUserManagement();
                                    } else {
                                        setTimeout(() => {
                                            if (typeof window.showUserManagement === 'function') window.showUserManagement();
                                            else console.error('showUserManagement not found');
                                        }, 200);
                                    }
                                };
                                callUserMgmt();
                            });
                        }
                        const iBtn = document.getElementById('incorrectMgmtBtn');
                        if (iBtn) {
                            iBtn.addEventListener('click', function(e) {
                                e.preventDefault();
                                showIncorrectManagement();
                            });
                        }
                    } catch (e) { console.error('Error attaching admin button handlers', e); }
                }
            } catch (e) {
                localStorage.removeItem('adminToken');
                window.location.href = '/admin';
            }
        })();
        
        async function adminLogout() {
            const token = localStorage.getItem('adminToken');
            if (token) {
                await fetch('/api/admin/logout?token=' + encodeURIComponent(token), { method: 'POST' });
            }
            localStorage.removeItem('adminToken');
            window.location.href = '/admin';
        }
        
        // User management: fetch list, delete, reset password
        let allUserRecords = [];

        async function showUserManagement() {
            const token = localStorage.getItem('adminToken');
            try {
                const resp = await fetch('/api/admin/users?token=' + encodeURIComponent(token));
                if (!resp.ok) throw new Error('Failed to fetch users');
                allUserRecords = await resp.json();
                
                const html = `
                <div class="records-container">
                    <h3>👥 使用者管理</h3>
                    <div class="records-toolbar">
                        <div>
                            <span id="userRecordCount">共 ${allUserRecords.length} 位使用者</span>
                        </div>
                    </div>
                    <div class="table-wrapper">
                        <table class="records-table">
                            <thead>
                                <tr>
                                    <th>ID</th>
                                    <th>使用者名稱</th>
                                    <th>建立時間</th>
                                    <th>操作</th>
                                </tr>
                            </thead>
                            <tbody id="userRecordsBody">
                            </tbody>
                        </table>
                    </div>
                    <div style="text-align: center; margin-top: 20px;">
                        <button onclick="resetExam()" style="background-color: #4CAF50; color: white; border: none; padding: 12px 30px; font-size: 16px; cursor: pointer; border-radius: 4px;">關閉</button>
                    </div>
                </div>`;
                
                document.getElementById('examArea').innerHTML = html;
                renderUserRecordsTable(allUserRecords);
            } catch (e) {
                console.error('Error loading users:', e);
                alert('無法取得使用者列表');
            }
        }

        function renderUserRecordsTable(users) {
            const tbody = document.getElementById('userRecordsBody');
            if (!tbody) return;
            
            tbody.innerHTML = users.map(u => `
                <tr id="user-row-${u.id}">
                    <td>${u.id}</td>
                    <td>${u.username}</td>
                    <td>${u.created_at || ''}</td>
                    <td>
                        <button class="btn-edit" onclick="adminResetPassword(${u.id})">重設密碼</button>
                        <button class="btn-delete" onclick="adminDeleteUser(${u.id})">刪除</button>
                    </td>
                </tr>
            `).join('');
            
            document.getElementById('userRecordCount').textContent = `共 ${users.length} 位使用者`;
        }

        async function adminDeleteUser(id) {
            if (!confirm('確定要刪除此使用者嗎?')) return;
            const token = localStorage.getItem('adminToken');
            try {
                const resp = await fetch('/api/admin/users/delete?user_id=' + encodeURIComponent(id) + '&token=' + encodeURIComponent(token), { method: 'POST' });
                if (resp.ok) {
                    alert('已刪除');
                    showUserManagement();
                } else {
                    alert('刪除失敗');
                }
            } catch (e) { alert('刪除錯誤'); }
        }

        async function adminResetPassword(id) {
            const newPass = prompt('輸入新密碼 (留空使用預設 admin123):');
            const token = localStorage.getItem('adminToken');
            const passParam = encodeURIComponent(newPass || 'admin123');
            try {
                const resp = await fetch('/api/admin/users/reset-password?user_id=' + encodeURIComponent(id) + '&new_password=' + passParam + '&token=' + encodeURIComponent(token), { method: 'POST' });
                if (resp.ok) { alert('密碼已重設'); } else { alert('重設失敗'); }
            } catch (e) { alert('重設錯誤'); }
        }

        // Incorrect answer management
        let allIncorrectRecords = [];

        async function showIncorrectManagement(filterUser = '', filterUnit = '') {
            try {
                let url = '/api/incorrect?limit=2000';
                if (filterUser) url += '&username=' + encodeURIComponent(filterUser);
                if (filterUnit) url += '&unit=' + encodeURIComponent(filterUnit);
                
                const resp = await fetch(url);
                if (!resp.ok) throw new Error('Failed to fetch incorrect records');
                allIncorrectRecords = await resp.json();
                
                // Get unique users and units for filters
                const users = [...new Set(allIncorrectRecords.map(r => r.username))].sort();
                const units = [...new Set(allIncorrectRecords.map(r => r.unit_number))].sort((a, b) => parseInt(a) - parseInt(b));
                
                const html = `
                <div class="records-container">
                    <h3>📝 錯字管理</h3>
                    <div class="records-toolbar">
                        <div>
                            <label>篩選用戶:</label>
                            <select id="incorrectFilterUser" onchange="filterIncorrectRecords()">
                                <option value="">全部用戶</option>
                                ${users.map(u => `<option value="${u}" ${u === filterUser ? 'selected' : ''}>${u}</option>`).join('')}
                            </select>
                            <label>Unit:</label>
                            <select id="incorrectFilterUnit" onchange="filterIncorrectRecords()">
                                <option value="">全部Unit</option>
                                ${units.map(u => `<option value="${u}" ${u === filterUnit ? 'selected' : ''}>${u}</option>`).join('')}
                            </select>
                        </div>
                        <div>
                            <span id="incorrectRecordCount">共 ${allIncorrectRecords.length} 筆紀錄</span>
                        </div>
                    </div>
                    <div class="table-wrapper">
                        <table class="records-table">
                            <thead>
                                <tr>
                                    <th>ID</th>
                                    <th>用戶</th>
                                    <th>Unit</th>
                                    <th>中文</th>
                                    <th>英文</th>
                                    <th>時間</th>
                                    <th>操作</th>
                                </tr>
                            </thead>
                            <tbody id="incorrectRecordsBody">
                            </tbody>
                        </table>
                    </div>
                    <div style="text-align: center; margin-top: 20px;">
                        <button onclick="resetExam()" style="background-color: #4CAF50; color: white; border: none; padding: 12px 30px; font-size: 16px; cursor: pointer; border-radius: 4px;">關閉</button>
                    </div>
                </div>`;
                
                document.getElementById('examArea').innerHTML = html;
                renderIncorrectRecordsTable(allIncorrectRecords);
            } catch (e) {
                console.error('Error loading incorrect records:', e);
                alert('無法載入錯字紀錄');
            }
        }

        function renderIncorrectRecordsTable(records) {
            const tbody = document.getElementById('incorrectRecordsBody');
            if (!tbody) return;
            
            tbody.innerHTML = records.map(r => `
                <tr id="incorrect-row-${r.id}">
                    <td>${r.id}</td>
                    <td>${r.username || ''}</td>
                    <td id="incorrect-cell-unit-${r.id}">${r.unit_number}</td>
                    <td id="incorrect-cell-chinese-${r.id}">${r.Chinese}</td>
                    <td id="incorrect-cell-english-${r.id}">${r.English}</td>
                    <td>${r.last_incorrect || ''}</td>
                    <td id="incorrect-cell-actions-${r.id}">
                        <button class="btn-edit" onclick="startEditIncorrect(${r.id})">編輯</button>
                        <button class="btn-delete" onclick="deleteIncorrect(${r.id})">刪除</button>
                    </td>
                </tr>
            `).join('');
            
            document.getElementById('incorrectRecordCount').textContent = `共 ${records.length} 筆紀錄`;
        }

        function filterIncorrectRecords() {
            const userFilter = document.getElementById('incorrectFilterUser').value;
            const unitFilter = document.getElementById('incorrectFilterUnit').value;
            showIncorrectManagement(userFilter, unitFilter);
        }

        function startEditIncorrect(id) {
            const record = allIncorrectRecords.find(r => r.id === id);
            if (!record) return;
            
            document.getElementById(`incorrect-cell-unit-${id}`).innerHTML = 
                `<input type="text" id="edit-incorrect-unit-${id}" value="${record.unit_number}" style="width:50px;">`;
            document.getElementById(`incorrect-cell-chinese-${id}`).innerHTML = 
                `<input type="text" id="edit-incorrect-chinese-${id}" value="${record.Chinese}" style="width:100px;">`;
            document.getElementById(`incorrect-cell-english-${id}`).innerHTML = 
                `<input type="text" id="edit-incorrect-english-${id}" value="${record.English}" style="width:100px;">`;
            document.getElementById(`incorrect-cell-actions-${id}`).innerHTML = 
                `<button class="btn-save" onclick="saveEditIncorrect(${id})">儲存</button>
                 <button class="btn-cancel" onclick="cancelEditIncorrect(${id})">取消</button>`;
        }

        function cancelEditIncorrect(id) {
            const record = allIncorrectRecords.find(r => r.id === id);
            if (!record) return;
            
            document.getElementById(`incorrect-cell-unit-${id}`).innerHTML = record.unit_number;
            document.getElementById(`incorrect-cell-chinese-${id}`).innerHTML = record.Chinese;
            document.getElementById(`incorrect-cell-english-${id}`).innerHTML = record.English;
            document.getElementById(`incorrect-cell-actions-${id}`).innerHTML = 
                `<button class="btn-edit" onclick="startEditIncorrect(${id})">編輯</button>
                 <button class="btn-delete" onclick="deleteIncorrect(${id})">刪除</button>`;
        }

        async function saveEditIncorrect(id) {
            const unitNumber = document.getElementById(`edit-incorrect-unit-${id}`).value;
            const chinese = document.getElementById(`edit-incorrect-chinese-${id}`).value;
            const english = document.getElementById(`edit-incorrect-english-${id}`).value;
            
            try {
                const resp = await fetch(`/api/incorrect/${id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        unit_number: unitNumber,
                        chinese: chinese,
                        english: english
                    })
                });
                
                if (!resp.ok) throw new Error('Failed to update');
                
                const updated = await resp.json();
                const index = allIncorrectRecords.findIndex(r => r.id === id);
                if (index !== -1) {
                    allIncorrectRecords[index] = updated;
                }
                
                // Re-render
                const userFilter = document.getElementById('incorrectFilterUser')?.value || '';
                const unitFilter = document.getElementById('incorrectFilterUnit')?.value || '';
                showIncorrectManagement(userFilter, unitFilter);
            } catch (e) {
                console.error('Error updating incorrect record:', e);
                alert('更新失敗');
            }
        }

        async function deleteIncorrect(id) {
            if (!confirm('確定要刪除這筆錯字紀錄嗎？')) return;
            
            try {
                const resp = await fetch(`/api/incorrect/${id}`, { method: 'DELETE' });
                if (!resp.ok) throw new Error('Failed to delete');
                
                allIncorrectRecords = allIncorrectRecords.filter(r => r.id !== id);
                
                // Re-render
                const userFilter = document.getElementById('incorrectFilterUser')?.value || '';
                const unitFilter = document.getElementById('incorrectFilterUnit')?.value || '';
                showIncorrectManagement(userFilter, unitFilter);
            } catch (e) {
                console.error('Error deleting incorrect record:', e);
                alert('刪除失敗');
            }
        }

        // Database Export/Import functions
        async function exportDatabase() {
            const token = localStorage.getItem('adminToken');
            try {
                const resp = await fetch('/api/admin/export-db?token=' + encodeURIComponent(token));
                if (!resp.ok) {
                    const err = await resp.json();
                    throw new Error(err.detail || 'Export failed');
                }
                
                // Get filename from Content-Disposition header or generate one
                const disposition = resp.headers.get('Content-Disposition');
                let filename = 'aces_backup.json';
                if (disposition) {
                    const match = disposition.match(/filename=([^;]+)/);
                    if (match) filename = match[1];
                }
                
                // Download the file
                const blob = await resp.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = filename;
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(url);
                a.remove();
                
                alert('資料庫已成功匯出！');
            } catch (e) {
                console.error('Export error:', e);
                alert('匯出失敗: ' + e.message);
            }
        }

        async function importDatabase(input) {
            const file = input.files[0];
            if (!file) return;
            
            if (!confirm('確定要匯入資料庫嗎？\\n\\n此操作會將備份檔案中的資料合併至現有資料庫（不會刪除現有資料）。\\n重複的資料將被跳過。')) {
                input.value = '';
                return;
            }
            
            const token = localStorage.getItem('adminToken');
            const formData = new FormData();
            formData.append('file', file);
            
            try {
                const resp = await fetch('/api/admin/import-db?token=' + encodeURIComponent(token), {
                    method: 'POST',
                    body: formData
                });
                
                const result = await resp.json();
                
                if (!resp.ok) {
                    throw new Error(result.detail || 'Import failed');
                }
                
                let message = '資料庫匯入成功！\\n\\n匯入統計：\\n';
                if (result.imported) {
                    for (const [table, count] of Object.entries(result.imported)) {
                        message += `- ${table}: ${count} 筆新資料\\n`;
                    }
                }
                
                alert(message);
                input.value = '';
            } catch (e) {
                console.error('Import error:', e);
                alert('匯入失敗: ' + e.message);
                input.value = '';
            }
        }
    </script>
</body>'''
    html_content = html_content.replace("</body>", admin_script)
    
    return HTMLResponse(content=html_content)


def require_admin(token: str):
    if not verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Not authenticated")


@app.get("/api/admin/users")
async def admin_list_users(token: str = ""):
    """Return list of users (username, created_at). Admin only."""
    require_admin(token)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(sql('SELECT id, username, created_at FROM users ORDER BY username'))
    rows = cursor.fetchall()
    conn.close()
    return [{"id": row['id'], "username": row['username'], "created_at": format_exam_date(row['created_at'])} for row in rows]


@app.post("/api/admin/users/delete")
async def admin_delete_user(user_id: int, token: str = ""):
    """Delete a user by id. Admin only."""
    require_admin(token)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(sql('DELETE FROM users WHERE id = ?'), (user_id,))
    conn.commit()
    conn.close()
    return {"success": True, "id": user_id}


@app.post("/api/admin/users/reset-password")
async def admin_reset_password(user_id: int, new_password: str = "admin123", token: str = ""):
    """Reset user's password to a provided password (default 'admin123'). Admin only."""
    require_admin(token)
    new_hash = hashlib.sha256(new_password.encode()).hexdigest()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(sql('UPDATE users SET password_hash = ? WHERE id = ?'), (new_hash, user_id))
    conn.commit()
    conn.close()
    return {"success": True, "id": user_id}


@app.get("/api/admin/export-db")
async def export_database(token: str = ""):
    """Export all database tables as JSON for backup. Admin only."""
    require_admin(token)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    export_data = {
        "export_date": datetime.now(timezone.utc).isoformat(),
        "tables": {}
    }
    
    # Export exam_results
    cursor.execute('SELECT * FROM exam_results ORDER BY id')
    rows = cursor.fetchall()
    export_data["tables"]["exam_results"] = [
        {
            "id": row['id'],
            "username": row['username'],
            "unit_number": row['unit_number'],
            "score": row['score'],
            "type_accuracy": row['type_accuracy'],
            "correct_count": row['correct_count'],
            "total_questions": row['total_questions'],
            "exam_date": format_exam_date(row['exam_date'])
        } for row in rows
    ]
    
    # Export users
    cursor.execute('SELECT * FROM users ORDER BY id')
    rows = cursor.fetchall()
    export_data["tables"]["users"] = [
        {
            "id": row['id'],
            "username": row['username'],
            "password_hash": row['password_hash'],
            "created_at": format_exam_date(row['created_at'])
        } for row in rows
    ]
    
    # Export admin_settings
    cursor.execute('SELECT * FROM admin_settings ORDER BY id')
    rows = cursor.fetchall()
    export_data["tables"]["admin_settings"] = [
        {
            "id": row['id'],
            "setting_key": row['setting_key'],
            "setting_value": row['setting_value']
        } for row in rows
    ]
    
    # Export incorrect_answers
    cursor.execute('SELECT * FROM incorrect_answers ORDER BY id')
    rows = cursor.fetchall()
    export_data["tables"]["incorrect_answers"] = [
        {
            "id": row['id'],
            "username": row['username'],
            "unit_number": row['unit_number'],
            "chinese": row['chinese'],
            "english": row['english'],
            "last_incorrect": format_exam_date(row['last_incorrect'])
        } for row in rows
    ]
    
    conn.close()
    
    # Return as downloadable JSON file
    json_str = json.dumps(export_data, ensure_ascii=False, indent=2)
    return StreamingResponse(
        io.BytesIO(json_str.encode('utf-8')),
        media_type="application/json",
        headers={
            "Content-Disposition": f"attachment; filename=aces_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        }
    )


@app.post("/api/admin/import-db")
async def import_database(token: str = "", file: UploadFile = File(...)):
    """Import database from JSON backup file. Admin only. This will MERGE data (not replace)."""
    require_admin(token)
    
    try:
        content = await file.read()
        import_data = json.loads(content.decode('utf-8'))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON file: {str(e)}")
    
    if "tables" not in import_data:
        raise HTTPException(status_code=400, detail="Invalid backup file format")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    imported_counts = {}
    
    try:
        # Import users (skip if username already exists)
        if "users" in import_data["tables"]:
            count = 0
            for user in import_data["tables"]["users"]:
                try:
                    cursor.execute(sql('SELECT id FROM users WHERE username = ?'), (user['username'],))
                    if cursor.fetchone() is None:
                        cursor.execute(sql('''
                            INSERT INTO users (username, password_hash)
                            VALUES (?, ?)
                        '''), (user['username'], user['password_hash']))
                        count += 1
                except Exception:
                    continue
            imported_counts["users"] = count
        
        # Import exam_results (check for duplicates by username + exam_date)
        if "exam_results" in import_data["tables"]:
            count = 0
            for result in import_data["tables"]["exam_results"]:
                try:
                    # Check for duplicate (same user, unit, and similar time)
                    cursor.execute(sql('''
                        SELECT id FROM exam_results 
                        WHERE username = ? AND unit_number = ? AND score = ? AND correct_count = ?
                    '''), (result['username'], result['unit_number'], result['score'], result['correct_count']))
                    if cursor.fetchone() is None:
                        cursor.execute(sql('''
                            INSERT INTO exam_results (username, unit_number, score, type_accuracy, correct_count, total_questions)
                            VALUES (?, ?, ?, ?, ?, ?)
                        '''), (
                            result['username'],
                            result['unit_number'],
                            result['score'],
                            result['type_accuracy'],
                            result['correct_count'],
                            result['total_questions']
                        ))
                        count += 1
                except Exception:
                    continue
            imported_counts["exam_results"] = count
        
        # Import incorrect_answers
        if "incorrect_answers" in import_data["tables"]:
            count = 0
            for item in import_data["tables"]["incorrect_answers"]:
                try:
                    # Check for duplicate
                    cursor.execute(sql('''
                        SELECT id FROM incorrect_answers 
                        WHERE username = ? AND unit_number = ? AND chinese = ? AND english = ?
                    '''), (item['username'], item['unit_number'], item['chinese'], item['english']))
                    if cursor.fetchone() is None:
                        cursor.execute(sql('''
                            INSERT INTO incorrect_answers (username, unit_number, chinese, english)
                            VALUES (?, ?, ?, ?)
                        '''), (
                            item['username'],
                            item['unit_number'],
                            item['chinese'],
                            item['english']
                        ))
                        count += 1
                except Exception:
                    continue
            imported_counts["incorrect_answers"] = count
        
        # Import admin_settings (update if key exists, insert if not)
        if "admin_settings" in import_data["tables"]:
            count = 0
            for setting in import_data["tables"]["admin_settings"]:
                try:
                    if USE_POSTGRES:
                        cursor.execute('''
                            INSERT INTO admin_settings (setting_key, setting_value)
                            VALUES (%s, %s)
                            ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value
                        ''', (setting['setting_key'], setting['setting_value']))
                    else:
                        cursor.execute('''
                            INSERT OR REPLACE INTO admin_settings (setting_key, setting_value)
                            VALUES (?, ?)
                        ''', (setting['setting_key'], setting['setting_value']))
                    count += 1
                except Exception:
                    continue
            imported_counts["admin_settings"] = count
        
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=500, detail=f"Import failed: {str(e)}")
    
    conn.close()
    
    return {
        "success": True,
        "message": "Database imported successfully",
        "imported": imported_counts
    }


@app.get("/{filename}")
async def read_file(filename: str):
    """Serve static files like CSV and HTML."""
    if os.path.exists(filename):
        return FileResponse(filename)
    raise HTTPException(status_code=404, detail="File not found")


if __name__ == "__main__":
    import uvicorn
    print("Starting ACES Unit Test API Server...")
    print("Open http://localhost:8000 in your browser")
    uvicorn.run(app, host="0.0.0.0", port=8000)
