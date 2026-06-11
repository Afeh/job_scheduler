import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from dotenv import load_dotenv

load_dotenv()

# Secure Coding: Ensure the database connection uses safe defaults.
# For SQLite, it is a local file. SQLAlchemy handles parameterized queries.
# Override via DATABASE_URL environment variable for production (e.g. PostgreSQL).
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./scheduler.db")

# SQLite-specific connect args (not needed for PostgreSQL/MySQL)
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
