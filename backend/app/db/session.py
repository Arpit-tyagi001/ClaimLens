from sqlmodel import SQLModel, create_engine, Session, text
from backend.app.db.models import *  # Imports your models so SQLModel knows about them

sqlite_file_name = "claimlens_local.db"
sqlite_url = f"sqlite:///{sqlite_file_name}"

# check_same_thread is needed for FastAPI & SQLite
connect_args = {"check_same_thread": False}
engine = create_engine(sqlite_url, echo=True, connect_args=connect_args)


def create_db_and_tables(target_engine=None):
    eng = target_engine or engine
    SQLModel.metadata.create_all(eng)
    with eng.connect() as conn:
        conn.execute(text("""
            CREATE TRIGGER IF NOT EXISTS prevent_audit_log_update
            BEFORE UPDATE ON audit_log
            BEGIN
                SELECT RAISE(ABORT, 'audit_log is append-only');
            END;
        """))
        conn.execute(text("""
            CREATE TRIGGER IF NOT EXISTS prevent_audit_log_delete
            BEFORE DELETE ON audit_log
            BEGIN
                SELECT RAISE(ABORT, 'audit_log is append-only');
            END;
        """))
        conn.commit()
    # TODO: Add Postgres trigger version (BEFORE UPDATE OR DELETE ON audit_log EXECUTE FUNCTION prevent_audit_log_mutation())


def get_session():
    with Session(engine) as session:
        yield session