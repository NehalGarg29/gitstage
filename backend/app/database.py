import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://gitstage:gitstage_secure_pass_123@db:5432/gitstage_db"
)

# Use create_engine. For PostgreSQL, no special arguments are strictly required, 
# but pool size configurations are helpful for handling multiple connection pools.
engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
