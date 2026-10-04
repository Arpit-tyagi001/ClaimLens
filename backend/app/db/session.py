from sqlmodel import SQLModel, create_engine, Session
from backend.app.db.models import *  # Imports your models so SQLModel knows about them

sqlite_file_name = "claimlens_local.db"
sqlite_url = f"sqlite:///{sqlite_file_name}"

# check_same_thread is needed for FastAPI & SQLite
connect_args = {"check_same_thread": False}
engine = create_engine(sqlite_url, echo=True, connect_args=connect_args)

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session