"""Minimal GET /team endpoint returning hardcoded team data."""
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

app = FastAPI()

TEAM = [
    {"name": "Ada Okafor", "role": "Backend Engineer", "fun_fact": "Once debugged a race condition in her sleep."},
    {"name": "Marco Lindgren", "role": "Frontend Engineer", "fun_fact": "Has never used a mouse, only trackpad."},
    {"name": "Priya Raman", "role": "QA Engineer", "fun_fact": "Finds bugs faster than she finds parking spots."},
]


@app.get("/team")
def get_team():
    return TEAM


@app.get("/")
def get_index():
    return FileResponse(Path(__file__).parent / "team.html")
