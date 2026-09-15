"""Create the AIOS SQLite schema. Run once per deployment: python scripts/init_db.py"""
from core.state import init_db

if __name__ == "__main__":
    init_db()
    print("AIOS database initialized.")
