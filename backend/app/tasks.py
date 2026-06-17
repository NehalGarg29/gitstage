import os
import json
import math
import random
import logging
import time
import jwt
import requests
import sys
from celery import Celery
import redis
from sqlalchemy.orm import Session
from openai import OpenAI

from app.database import SessionLocal, engine
from app.models import Repository, RepositoryFile, CodeChunk
from app.ast_parser import parse_python_file
from app.pubsub import pubsub_manager

logger = logging.getLogger(__name__)

# Configure Celery
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
celery_app = Celery("tasks", broker=REDIS_URL, backend=REDIS_URL)

# Redis client for progress publishing
redis_client = redis.Redis.from_url(REDIS_URL)

# OpenAI Client
openai_client = None
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "mock")
if OPENAI_API_KEY and OPENAI_API_KEY != "mock":
    openai_client = OpenAI(api_key=OPENAI_API_KEY)

def get_github_installation_token(installation_id: int) -> str:
    """Uses App ID and RSA private key to generate a JWT and request an installation token."""
    app_id = os.getenv("GITHUB_APP_ID", "")
    private_key = os.getenv("GITHUB_PRIVATE_KEY", "")
    
    if not private_key:
        pem_path = "/app/gitstage-app.private-key.pem"
        if os.path.exists(pem_path):
            try:
                with open(pem_path, "r") as f:
                    private_key = f.read()
            except Exception as e:
                logger.error(f"Error reading private key file: {e}")
                
    if not app_id or not private_key:
        logger.warning("GitHub App credentials missing (GITHUB_APP_ID or GITHUB_PRIVATE_KEY). Falling back to mock token.")
        return "mock_token"

    payload = {
        "iat": int(time.time()) - 60,
        "exp": int(time.time()) + (10 * 60),
        "iss": int(app_id)
    }
    
    try:
        app_jwt = jwt.encode(payload, private_key, algorithm="RS256")
    except Exception as e:
        logger.error(f"Failed to encode JWT: {e}. Falling back to mock token.")
        return "mock_token"

    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "GitStage-App"
    }
    try:
        res = requests.post(url, headers=headers)
        res.raise_for_status()
        return res.json()["token"]
    except Exception as e:
        logger.error(f"Failed to fetch installation token from GitHub: {e}")
        return "mock_token"

def get_mock_embedding():
    """Generates a random normalized 1536-dimension vector for offline testing."""
    vec = [random.gauss(0, 1) for _ in range(1536)]
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec]

def get_openai_embedding(text: str) -> list:
    """Gets embedding from OpenAI or falls back to mock embedding if key is missing."""
    if not openai_client:
        return get_mock_embedding()
    try:
        response = openai_client.embeddings.create(
            input=[text],
            model="text-embedding-3-small"
        )
        return response.data[0].embedding
    except Exception as e:
        logger.error(f"OpenAI embedding error: {e}. Falling back to mock embedding.")
        return get_mock_embedding()

def publish_progress(repo_id: int, status: str, progress: int, message: str):
    """Publishes progress update to both Redis (if available) and the in-memory PubSub system."""
    channel = f"repo_progress_{repo_id}"
    data = {
        "status": status,
        "progress": progress,
        "message": message
    }
    msg_str = json.dumps(data)
    
    # 1. Try publishing to Redis (wrapped to catch offline/mock environment errors)
    try:
        redis_client.publish(channel, msg_str)
    except Exception as e:
        logger.debug(f"Redis publish connection issue (expected in local/free-tier): {e}")

    # 2. Publish to the in-memory pubsub_manager safely in the main event loop
    try:
        main_module = sys.modules.get("app.main")
        if main_module and getattr(main_module, "main_loop", None):
            loop = main_module.main_loop
            if loop.is_running():
                loop.call_soon_threadsafe(pubsub_manager.publish, channel, msg_str)
            else:
                pubsub_manager.publish(channel, msg_str)
        else:
            pubsub_manager.publish(channel, msg_str)
    except Exception as e:
        logger.warning(f"Failed to publish in-memory progress event: {e}")


@celery_app.task(name="app.tasks.ingest_repository_task")
def ingest_repository_task(repo_id: int, local_dir_path: str):
    """
    Ingests a repository from a local folder path, parses all .py files, 
    generates embeddings, and stores them in the DB.
    """
    logger.info(f"Starting ingestion for repo {repo_id} from {local_dir_path}")
    db: Session = SessionLocal()
    
    try:
        # 1. Update Repo Status to indexing
        repo = db.query(Repository).filter(Repository.id == repo_id).first()
        if not repo:
            logger.error(f"Repository {repo_id} not found in database.")
            return False
            
        repo.status = "indexing"
        repo.error_message = None
        db.commit()
        
        publish_progress(repo_id, "indexing", 10, "Scanning local directory structure...")
        
        # 2. Find all python files, ignoring standard folders
        python_files = []
        ignored_dirs = {".git", "__pycache__", "node_modules", "venv", "env", ".pytest_cache", "build", "dist"}
        
        for root, dirs, files in os.walk(local_dir_path):
            # Prune ignored directories in-place
            dirs[:] = [d for d in dirs if d not in ignored_dirs]
            
            for file in files:
                if file.endswith(".py"):
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, local_dir_path)
                    python_files.append((full_path, rel_path))
                    
        total_files = len(python_files)
        if total_files == 0:
            repo.status = "synced"
            db.commit()
            publish_progress(repo_id, "synced", 100, "Synced successfully (No Python files found).")
            return True
            
        publish_progress(repo_id, "indexing", 20, f"Found {total_files} Python files. Commencing AST parsing...")
        
        # 3. Process each python file
        for idx, (full_path, rel_path) in enumerate(python_files):
            try:
                with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                
                # Check if file already exists in DB
                db_file = db.query(RepositoryFile).filter(
                    RepositoryFile.repository_id == repo_id,
                    RepositoryFile.filepath == rel_path
                ).first()
                
                if db_file:
                    # Clear existing chunks for this file
                    db.query(CodeChunk).filter(CodeChunk.file_id == db_file.id).delete()
                else:
                    db_file = RepositoryFile(
                        repository_id=repo_id,
                        filepath=rel_path,
                        size=os.path.getsize(full_path)
                    )
                    db.add(db_file)
                    db.flush() # Populate db_file.id
                
                # Parse AST
                chunks = parse_python_file(content, rel_path)
                
                for chunk in chunks:
                    # Generate embedding
                    embedding = get_openai_embedding(chunk["code_content"])
                    
                    db_chunk = CodeChunk(
                        repository_id=repo_id,
                        file_id=db_file.id,
                        name=chunk["name"],
                        type=chunk["type"],
                        start_line=chunk["start_line"],
                        end_line=chunk["end_line"],
                        code_content=chunk["code_content"],
                        embedding=embedding
                    )
                    db.add(db_chunk)
                
                db.commit()
                
                # Update progress
                progress_percent = int(20 + (idx + 1) / total_files * 70)
                publish_progress(
                    repo_id, 
                    "indexing", 
                    progress_percent, 
                    f"Processed {rel_path} ({idx + 1}/{total_files})"
                )
                
            except Exception as file_err:
                logger.error(f"Error processing file {rel_path}: {file_err}")
                # Continue with other files
                
        # 4. Finalize
        repo.status = "synced"
        db.commit()
        publish_progress(repo_id, "synced", 100, f"Sync complete! Fully indexed {total_files} files.")
        logger.info(f"Ingestion successful for repo {repo_id}")
        return True
        
    except Exception as e:
        logger.error(f"Ingestion failed for repo {repo_id}: {e}")
        db.rollback()
        
        # Mark repo as failed
        repo = db.query(Repository).filter(Repository.id == repo_id).first()
        if repo:
            repo.status = "failed"
            repo.error_message = str(e)
            db.commit()
            
        publish_progress(repo_id, "failed", 100, f"Error: {e}")
        return False
    finally:
        db.close()


@celery_app.task(name="app.tasks.ingest_github_repository_task")
def ingest_github_repository_task(repo_id: int, installation_id: int):
    """
    Ingests a repository directly from the GitHub API using installation credentials,
    recursively reads all Python files, chunks them, and stores their vector embeddings.
    """
    logger.info(f"Starting GitHub API ingestion for repo {repo_id} using installation {installation_id}")
    db: Session = SessionLocal()
    
    try:
        # 1. Fetch Repository Details
        repo = db.query(Repository).filter(Repository.id == repo_id).first()
        if not repo:
            logger.error(f"Repository {repo_id} not found in database.")
            return False
            
        repo.status = "indexing"
        repo.github_installation_id = installation_id
        repo.error_message = None
        db.commit()
        
        publish_progress(repo_id, "indexing", 5, "Requesting GitHub App credentials...")
        
        # 2. Get Installation Access Token
        token = get_github_installation_token(installation_id)
        
        # If credentials aren't set, fallback to indexing the backend container path '/app' (offline mode demo)
        if token == "mock_token":
            logger.warning("GitHub credentials are mock. Falling back to mock local ingestion of '/app'...")
            db.close()
            return ingest_repository_task(repo_id, "/app")

        publish_progress(repo_id, "indexing", 15, "Fetching repository file structure from GitHub...")
        
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "GitStage-App"
        }
        
        # Determine default branch
        repo_url = f"https://api.github.com/repos/{repo.full_name}"
        res = requests.get(repo_url, headers=headers)
        res.raise_for_status()
        default_branch = res.json().get("default_branch", "main")
        
        # Get recursive Git tree
        tree_url = f"https://api.github.com/repos/{repo.full_name}/git/trees/{default_branch}?recursive=1"
        res = requests.get(tree_url, headers=headers)
        res.raise_for_status()
        tree_data = res.json()
        
        files_to_index = []
        ignored_dirs = {".git", "__pycache__", "node_modules", "venv", "env", ".pytest_cache", "build", "dist"}
        
        for item in tree_data.get("tree", []):
            path = item.get("path", "")
            item_type = item.get("type", "")
            
            # Filter criteria: must be a file (blob), end with .py, and not in ignored directories
            if item_type == "blob" and path.endswith(".py"):
                parts = path.split("/")
                if not any(part in ignored_dirs for part in parts):
                    files_to_index.append(item)
                    
        total_files = len(files_to_index)
        if total_files == 0:
            repo.status = "synced"
            db.commit()
            publish_progress(repo_id, "synced", 100, "Synced successfully (No Python files found).")
            return True
            
        publish_progress(repo_id, "indexing", 25, f"Found {total_files} Python files. Downloading and chunking...")
        
        # 3. Process each python file
        for idx, item in enumerate(files_to_index):
            rel_path = item["path"]
            blob_sha = item["sha"]
            file_size = item.get("size", 0)
            blob_url = item["url"]
            
            try:
                # Fetch raw file bytes from GitHub
                raw_headers = headers.copy()
                raw_headers["Accept"] = "application/vnd.github.v3.raw"
                
                file_res = requests.get(blob_url, headers=raw_headers)
                file_res.raise_for_status()
                content = file_res.text
                
                # Check/Add RepositoryFile
                db_file = db.query(RepositoryFile).filter(
                    RepositoryFile.repository_id == repo_id,
                    RepositoryFile.filepath == rel_path
                ).first()
                
                if db_file:
                    db.query(CodeChunk).filter(CodeChunk.file_id == db_file.id).delete()
                else:
                    db_file = RepositoryFile(
                        repository_id=repo_id,
                        filepath=rel_path,
                        sha=blob_sha,
                        size=file_size
                    )
                    db.add(db_file)
                    db.flush()
                
                # Parse AST
                chunks = parse_python_file(content, rel_path)
                
                for chunk in chunks:
                    embedding = get_openai_embedding(chunk["code_content"])
                    
                    db_chunk = CodeChunk(
                        repository_id=repo_id,
                        file_id=db_file.id,
                        name=chunk["name"],
                        type=chunk["type"],
                        start_line=chunk["start_line"],
                        end_line=chunk["end_line"],
                        code_content=chunk["code_content"],
                        embedding=embedding
                    )
                    db.add(db_chunk)
                
                db.commit()
                
                # Update progress
                progress_percent = int(25 + (idx + 1) / total_files * 70)
                publish_progress(
                    repo_id,
                    "indexing",
                    progress_percent,
                    f"Downloaded and parsed {rel_path} ({idx + 1}/{total_files})"
                )
                
            except Exception as file_err:
                logger.error(f"Error fetching/indexing file {rel_path}: {file_err}")
                # continue with other files
                
        # 4. Ingestion Complete
        repo.status = "synced"
        db.commit()
        publish_progress(repo_id, "synced", 100, f"Indexing complete! Syntactically structured {total_files} files.")
        logger.info(f"GitHub Ingestion successful for repo {repo_id}")
        return True
        
    except Exception as e:
        logger.error(f"GitHub Ingestion failed for repo {repo_id}: {e}")
        db.rollback()
        
        repo = db.query(Repository).filter(Repository.id == repo_id).first()
        if repo:
            repo.status = "failed"
            repo.error_message = str(e)
            db.commit()
            
        publish_progress(repo_id, "failed", 100, f"Error during ingestion: {e}")
        return False
    finally:
        db.close()
