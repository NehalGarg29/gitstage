import os
import sys
import logging
from sqlalchemy import text

# Add backend root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, engine, Base
from app.models import User, Repository, RepositoryFile, CodeChunk
from app.tasks import ingest_repository_task

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("seed")

def run_seed():
    logger.info("Initializing database extension and schema...")
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        # 1. Ensure mock dev user exists
        user = db.query(User).filter(User.username == "localdev").first()
        if not user:
            user = User(
                github_id=12345,
                username="localdev",
                email="dev@gitstage.local",
                subscription_tier="pro"
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            logger.info("Seeded dev user 'localdev'.")

        # 2. Add or Reset Repository
        repo_name = "gitstage-backend"
        full_name = f"localdev/{repo_name}"
        
        repo = db.query(Repository).filter(Repository.full_name == full_name).first()
        if repo:
            logger.info(f"Repository {full_name} already exists. Resetting database entries for fresh ingest...")
            db.query(CodeChunk).filter(CodeChunk.repository_id == repo.id).delete()
            db.query(RepositoryFile).filter(RepositoryFile.repository_id == repo.id).delete()
            repo.status = "pending"
            repo.error_message = None
            db.commit()
        else:
            repo = Repository(
                name=repo_name,
                full_name=full_name,
                owner_id=user.id,
                status="pending"
            )
            db.add(repo)
            db.commit()
            db.refresh(repo)
            logger.info(f"Seeded repository '{full_name}'.")

        # 3. Trigger Ingestion task synchronously
        logger.info("Starting synchronous codebase ingestion of 'app/'...")
        app_dir = os.path.dirname(os.path.abspath(__file__))
        
        # Run the ingestion function directly (synchronously)
        # instead of delay() to check for errors immediately.
        success = ingest_repository_task(repo.id, app_dir)
        
        if success:
            db.refresh(repo)
            files_count = db.query(RepositoryFile).filter(RepositoryFile.repository_id == repo.id).count()
            chunks_count = db.query(CodeChunk).filter(CodeChunk.repository_id == repo.id).count()
            logger.info("=========================================")
            logger.info("SEEDING COMPLETED SUCCESSFULLY!")
            logger.info(f"Repository Status: {repo.status}")
            logger.info(f"Files Indexed: {files_count}")
            logger.info(f"Code Chunks Embed: {chunks_count}")
            logger.info("=========================================")
        else:
            logger.error("Sync function returned failure status.")
            
    except Exception as e:
        logger.error(f"Seeding failed: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    run_seed()
