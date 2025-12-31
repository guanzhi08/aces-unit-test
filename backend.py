"""
FastAPI backend for ACES Unit Test Practice
- Records exam results with username in SQLite database
- Provides score history and curve data
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import sqlite3
import os

app = FastAPI(title="ACES Unit Test API")

# Enable CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database file path
DB_PATH = "exam_results.db"


def get_db_connection():
    """Create a database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize the database with required tables."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
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


class UserStats(BaseModel):
    username: str
    total_exams: int
    average_score: float
    average_type_accuracy: float
    best_score: float
    recent_scores: List[float]


@app.post("/api/results", response_model=ExamResultResponse)
async def save_exam_result(result: ExamResult):
    """Save an exam result to the database."""
    if not result.username.strip():
        raise HTTPException(status_code=400, detail="Username is required")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO exam_results (username, unit_number, score, type_accuracy, correct_count, total_questions)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (result.username.strip(), result.unit_number, result.score, result.type_accuracy, 
          result.correct_count, result.total_questions))
    
    result_id = cursor.lastrowid
    conn.commit()
    
    # Fetch the inserted record
    cursor.execute('SELECT * FROM exam_results WHERE id = ?', (result_id,))
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
        exam_date=row['exam_date']
    )


@app.get("/api/results/{username}", response_model=List[ExamResultResponse])
async def get_user_results(username: str, limit: int = 50):
    """Get exam results for a specific user."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT * FROM exam_results 
        WHERE username = ? 
        ORDER BY exam_date DESC 
        LIMIT ?
    ''', (username, limit))
    
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
        exam_date=row['exam_date']
    ) for row in rows]


@app.get("/api/curve/{username}", response_model=ScoreCurveData)
async def get_score_curve(username: str, unit: Optional[str] = None, limit: int = 20):
    """Get score curve data for a specific user (last N exams), optionally filtered by unit."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if unit:
        cursor.execute('''
            SELECT exam_date, score, type_accuracy 
            FROM exam_results 
            WHERE username = ? AND unit_number = ?
            ORDER BY exam_date ASC 
            LIMIT ?
        ''', (username, unit, limit))
    else:
        cursor.execute('''
            SELECT exam_date, score, type_accuracy 
            FROM exam_results 
            WHERE username = ? 
            ORDER BY exam_date ASC 
            LIMIT ?
        ''', (username, limit))
    
    rows = cursor.fetchall()
    conn.close()
    
    dates = [row['exam_date'][:16] for row in rows]  # Format: YYYY-MM-DD HH:MM
    scores = [row['score'] for row in rows]
    type_accuracies = [row['type_accuracy'] for row in rows]
    
    return ScoreCurveData(dates=dates, scores=scores, type_accuracies=type_accuracies)


@app.get("/api/curve-by-unit/{username}")
async def get_score_curve_by_unit(username: str):
    """Get score curve data grouped by unit for a specific user."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get all distinct units for this user
    cursor.execute('''
        SELECT DISTINCT unit_number FROM exam_results 
        WHERE username = ? 
        ORDER BY CAST(unit_number AS INTEGER)
    ''', (username,))
    units = [row['unit_number'] for row in cursor.fetchall()]
    
    # Get data for each unit
    result = {}
    for unit in units:
        cursor.execute('''
            SELECT exam_date, score, type_accuracy 
            FROM exam_results 
            WHERE username = ? AND unit_number = ?
            ORDER BY exam_date ASC
        ''', (username, unit))
        rows = cursor.fetchall()
        result[unit] = {
            'dates': [row['exam_date'][:16] for row in rows],
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
        cursor.execute('''
            SELECT 
                COUNT(*) as total_exams,
                AVG(score) as avg_score,
                AVG(type_accuracy) as avg_type_accuracy,
                MAX(score) as best_score
            FROM exam_results 
            WHERE username = ? AND unit_number = ?
        ''', (username, unit))
    else:
        cursor.execute('''
            SELECT 
                COUNT(*) as total_exams,
                AVG(score) as avg_score,
                AVG(type_accuracy) as avg_type_accuracy,
                MAX(score) as best_score
            FROM exam_results 
            WHERE username = ?
        ''', (username,))
    
    stats = cursor.fetchone()
    
    # Get recent scores for mini chart
    if unit:
        cursor.execute('''
            SELECT score FROM exam_results 
            WHERE username = ? AND unit_number = ?
            ORDER BY exam_date DESC 
            LIMIT 10
        ''', (username, unit))
    else:
        cursor.execute('''
            SELECT score FROM exam_results 
            WHERE username = ? 
            ORDER BY exam_date DESC 
            LIMIT 10
        ''', (username,))
    
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
