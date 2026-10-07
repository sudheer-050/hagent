"""Static catalog of the apps Sudheer has built, shown in Hagent's sidebar.

Each entry is an "about this app" page (name, what it does, how it was built)
with either an Open button (if it's live somewhere) or a Check it out button
(source only, not deployed).
"""

APPLICATIONS = [
    {
        "slug": "hagent",
        "name": "Hagent",
        "tagline": "Self-hosted workspace for human-directed AI agents",
        "description": (
            "A self-hosted multi-agent orchestration platform with pluggable AI runtimes "
            "(Claude, OpenAI, local Ollama) — project coordination, a kanban board, local "
            "knowledge retrieval, and optional terminal tools for agents. This is the app "
            "you're using right now."
        ),
        "built_with": "Python, FastAPI, SQLAlchemy, Jinja2",
        "status": "live",
        "open_label": "Open Hagent",
        "open_url": "http://127.0.0.1:8000/",
        "repo_url": "https://github.com/sudheer-050/hagent",
    },
    {
        "slug": "hai-messenger",
        "name": "HAI Messenger",
        "tagline": "Free, self-hosted, end-to-end encrypted chat app",
        "description": (
            "Real-time messaging, voice notes, photo/document sharing, live location, "
            "in-chat mini-games, an anonymous 24-hour \"Nearby\" chat mode, and a local-Ollama "
            "AI assistant — all self-hosted with no third-party servers."
        ),
        "built_with": "Node.js, Socket.IO, Redis, PostgreSQL, Docker",
        "status": "live",
        "open_label": "Open HAI Messenger",
        "open_url": "https://myhai.org",
        "repo_url": "https://github.com/sudheer-050/HAI-messenger",
    },
    {
        "slug": "auto-data-analyst",
        "name": "Auto Data Analyst",
        "tagline": "Upload a spreadsheet, get automatic cleaning, EDA, and ML",
        "description": (
            "A Flask app that takes a raw dataset and automatically cleans it, runs "
            "exploratory data analysis, trains baseline ML models, and answers plain-"
            "English questions about the data with a rule-based Q&A layer."
        ),
        "built_with": "Python, Flask, pandas, scikit-learn",
        "status": "live",
        "open_label": "Open Auto Data Analyst",
        "open_url": "https://myhai.org/analyst/",
        "repo_url": "https://github.com/sudheer-050/auto-data-analyst",
    },
    {
        "slug": "portfolio",
        "name": "Portfolio site",
        "tagline": "Resume and project portfolio",
        "description": (
            "Personal portfolio and resume site listing projects, experience, and education."
        ),
        "built_with": "HTML, CSS",
        "status": "live",
        "open_label": "Open portfolio site",
        "open_url": "https://sudheer-050.github.io/",
        "repo_url": "https://github.com/sudheer-050/sudheer-050.github.io",
    },
    {
        "slug": "bizpack",
        "name": "bizpack",
        "tagline": "Zero-friction spreadsheet cleaning + business formulas for Python",
        "description": (
            "A published PyPI package that cleans messy spreadsheets and adds intuitive "
            "business formulas (XLOOKUP, Pareto, month-over-month growth) on top of pandas."
        ),
        "built_with": "Python",
        "status": "code",
        "open_label": "View on PyPI",
        "open_url": "https://pypi.org/project/bizpack/",
        "repo_url": "https://github.com/sudheer-050/bizpack",
    },
    {
        "slug": "h-ai",
        "name": "H-AI (HollyAI)",
        "tagline": "Local, memory-aware AI assistant",
        "description": (
            "A local, memory-aware AI assistant built with Ollama and Llama 3, supporting "
            "multiple users with unique IDs, conversation history, and monthly keyword "
            "summaries so terminal-based chats stay personal and consistent over time."
        ),
        "built_with": "Python, Ollama, Llama 3",
        "status": "code",
        "open_label": None,
        "open_url": None,
        "repo_url": "https://github.com/sudheer-050/H-AI",
    },
    {
        "slug": "morningstar",
        "name": "MorningStar",
        "tagline": "NLP pipeline for industry classification",
        "description": (
            "An NLP pipeline using TF-IDF and LinearSVC to classify corporate text "
            "descriptions into 145 hierarchical GECS industries, with custom knowledge-based "
            "imputation for missing text and macro/micro F1 optimization for class imbalance."
        ),
        "built_with": "Python, pandas, scikit-learn",
        "status": "code",
        "open_label": None,
        "open_url": None,
        "repo_url": "https://github.com/sudheer-050/MorningStar",
    },
    {
        "slug": "job-portal",
        "name": "Job portal",
        "tagline": "Self-hosted personalized job search portal",
        "description": (
            "A self-hosted job search portal with multi-source job collection and "
            "explainable matching against your profile. Not deployed publicly yet."
        ),
        "built_with": "Python",
        "status": "code",
        "open_label": None,
        "open_url": None,
        "repo_url": "https://github.com/sudheer-050/job-portal",
    },
]


def get_application(slug: str) -> dict | None:
    return next((a for a in APPLICATIONS if a["slug"] == slug), None)
