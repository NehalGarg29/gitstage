import os
import json
import logging
import asyncio
import requests
import jwt
import datetime
from fastapi import FastAPI, Depends, HTTPException, status, WebSocket, WebSocketDisconnect, Header, Request, BackgroundTasks
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text
import redis.asyncio as aioredis
from openai import OpenAI
import stripe

from app.database import engine, Base, get_db
from app.models import Repository, RepositoryFile, CodeChunk, User
from app.tasks import ingest_repository_task, ingest_github_repository_task, get_openai_embedding
from app.pubsub import pubsub_manager

# Auth & Billing Constants
GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")
GITHUB_REDIRECT_URI = os.getenv("GITHUB_REDIRECT_URI", "http://localhost:8000/auth/github/callback")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")
JWT_SECRET = os.getenv("JWT_SECRET", "gitstage_super_secret_jwt_key_987654321")

# Stripe Configuration
STRIPE_API_KEY = os.getenv("STRIPE_API_KEY", "mock_stripe_key")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "mock_webhook_secret")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "price_mock_premium_15")
stripe.api_key = STRIPE_API_KEY
JWT_SECRET = os.getenv("JWT_SECRET", "gitstage_super_secret_jwt_key_987654321")

def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.datetime.utcnow() + datetime.timedelta(days=7)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET, algorithm="HS256")

def get_current_user(authorization: str = Header(None), db: Session = Depends(get_db)) -> User:
    """Dependency to retrieve current user via JWT or fallback to seeded localdev."""
    if not authorization or not authorization.startswith("Bearer "):
        # Fallback to seeded dev user for local development convenience
        user = db.query(User).filter(User.username == "localdev").first()
        if not user:
            raise HTTPException(status_code=401, detail="Not authenticated and mock user not found.")
        return user
        
    token = authorization.split(" ")[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        username = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="Token missing identity claim.")
        user = db.query(User).filter(User.username == username).first()
        if not user:
            raise HTTPException(status_code=401, detail="User not found.")
        return user
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Token invalid or expired.")


# Logging configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="GitStage API", version="1.0.0")

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For dev simplicity
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Redis client for async sub/pub (WebSockets)
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

# OpenAI Client Setup
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "mock")
openai_client = None
if OPENAI_API_KEY and OPENAI_API_KEY != "mock":
    openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Initialize Database
main_loop = None

@app.on_event("startup")
def startup_db():
    global main_loop
    main_loop = asyncio.get_running_loop()
    logger.info("Initializing database extension and schema...")
    try:
        with engine.connect() as conn:
            # Enable pgvector extension
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.commit()
        
        # Create all tables
        Base.metadata.create_all(bind=engine)
        
        # Seed a default mock user for local development
        db = next(get_db())
        mock_user = db.query(User).filter(User.github_id == 12345).first()
        if not mock_user:
            mock_user = User(
                github_id=12345,
                username="localdev",
                email="dev@gitstage.local",
                subscription_tier="pro"
            )
            db.add(mock_user)
            db.commit()
            logger.info("Local development mock user seeded.")
        db.close()
    except Exception as e:
        logger.error(f"Error during database initialization: {e}")

# Pydantic schemas
class RepositoryCreate(BaseModel):
    name: str
    owner_username: str
    local_path: str = "/app"  # Defaults to /app (the backend repo itself) for local dev testing

class ChatRequest(BaseModel):
    query: str

# Endpoints
@app.get("/")
def read_root():
    return {"status": "ok", "service": "GitStage Backend"}

# Auth endpoints
@app.get("/auth/github/login")
def github_login():
    url = f"https://github.com/login/oauth/authorize?client_id={GITHUB_CLIENT_ID}&redirect_uri={GITHUB_REDIRECT_URI}&scope=repo"
    return RedirectResponse(url)

@app.get("/auth/github/callback")
def github_callback(code: str = None, db: Session = Depends(get_db)):
    if not GITHUB_CLIENT_ID or not GITHUB_CLIENT_SECRET or not code:
        # Development mock callback flow when client credentials aren't set
        user = db.query(User).filter(User.username == "localdev").first()
        jwt_token = create_access_token({"sub": user.username, "id": user.id})
        return RedirectResponse(f"{FRONTEND_URL}/auth/callback?token={jwt_token}&username={user.username}&avatar_url={user.avatar_url or ''}")

    token_url = "https://github.com/login/oauth/access_token"
    payload = {
        "client_id": GITHUB_CLIENT_ID,
        "client_secret": GITHUB_CLIENT_SECRET,
        "code": code,
        "redirect_uri": GITHUB_REDIRECT_URI
    }
    headers = {"Accept": "application/json"}
    try:
        res = requests.post(token_url, json=payload, headers=headers)
        res_data = res.json()
        access_token = res_data.get("access_token")
        if not access_token:
            logger.error(f"GitHub token exchange failed: {res_data}")
            raise HTTPException(status_code=400, detail="Failed to retrieve access token from GitHub.")
            
        # Get User details
        user_url = "https://api.github.com/user"
        user_headers = {
            "Authorization": f"token {access_token}",
            "Accept": "application/json",
            "User-Agent": "GitStage-App"
        }
        user_res = requests.get(user_url, headers=user_headers)
        user_data = user_res.json()
        
        github_id = user_data.get("id")
        username = user_data.get("login")
        avatar_url = user_data.get("avatar_url")
        email = user_data.get("email")
        
        if not github_id or not username:
            raise HTTPException(status_code=400, detail="Invalid profile data from GitHub.")
            
        user = db.query(User).filter(User.github_id == github_id).first()
        if not user:
            user = User(
                github_id=github_id,
                username=username,
                email=email,
                avatar_url=avatar_url,
                github_access_token=access_token,
                subscription_tier="pro"
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        else:
            user.github_access_token = access_token
            user.username = username
            user.avatar_url = avatar_url
            db.commit()
            
        jwt_token = create_access_token({"sub": user.username, "id": user.id})
        return RedirectResponse(f"{FRONTEND_URL}/auth/callback?token={jwt_token}&username={username}&avatar_url={avatar_url or ''}")
    except Exception as e:
        logger.error(f"GitHub authentication error: {e}")
        raise HTTPException(status_code=500, detail=f"Authentication error: {e}")

@app.get("/auth/me")
def get_me(user: User = Depends(get_current_user)):
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "avatar_url": user.avatar_url,
        "subscription_tier": user.subscription_tier
    }

@app.post("/repos", response_model=dict)
def create_repository(
    repo_in: RepositoryCreate, 
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db), 
    user: User = Depends(get_current_user)
):
    # Feature Gating: Free tier users are limited to 1 repository max
    if user.subscription_tier == "free":
        existing_repos = db.query(Repository).filter(Repository.owner_id == user.id).count()
        if existing_repos >= 1:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Repository limit reached (1 repo max for Free tier). Upgrade to PRO to sync unlimited codebases!"
            )

    # Verify target local directory exists
    if not os.path.exists(repo_in.local_path):
        raise HTTPException(
            status_code=400, 
            detail=f"Local path '{repo_in.local_path}' does not exist on backend filesystem."
        )

    full_name = f"{repo_in.owner_username}/{repo_in.name}"
    
    # Check if repository already exists for this user
    repo = db.query(Repository).filter(
        Repository.full_name == full_name,
        Repository.owner_id == user.id
    ).first()
    
    if repo:
        repo.status = "pending"
        db.commit()
    else:
        repo = Repository(
            name=repo_in.name,
            full_name=full_name,
            owner_id=user.id,
            status="pending"
        )
        db.add(repo)
        db.commit()
        db.refresh(repo)

    # Launch background task via FastAPI BackgroundTasks
    background_tasks.add_task(ingest_repository_task, repo.id, repo_in.local_path)
    
    return {
        "message": "Ingestion job started.",
        "repository_id": repo.id,
        "full_name": repo.full_name,
        "status": repo.status
    }

@app.get("/repos")
def list_repositories(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    repos = db.query(Repository).filter(Repository.owner_id == user.id).all()
    return [{
        "id": r.id,
        "name": r.name,
        "full_name": r.full_name,
        "status": r.status,
        "error_message": r.error_message,
        "created_at": r.created_at
    } for r in repos]

@app.get("/repos/{repo_id}")
def get_repository(repo_id: int, db: Session = Depends(get_db)):
    repo = db.query(Repository).filter(Repository.id == repo_id).first()
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found.")
        
    files_count = db.query(RepositoryFile).filter(RepositoryFile.repository_id == repo_id).count()
    chunks_count = db.query(CodeChunk).filter(CodeChunk.repository_id == repo_id).count()
    
    return {
        "id": repo.id,
        "name": repo.name,
        "full_name": repo.full_name,
        "status": repo.status,
        "error_message": repo.error_message,
        "files_count": files_count,
        "chunks_count": chunks_count,
        "created_at": repo.created_at
    }

@app.get("/repos/{repo_id}/chunks")
def get_repository_chunks(repo_id: int, db: Session = Depends(get_db)):
    chunks = db.query(CodeChunk).filter(CodeChunk.repository_id == repo_id).limit(100).all()
    return [{
        "id": c.id,
        "name": c.name,
        "type": c.type,
        "start_line": c.start_line,
        "end_line": c.end_line,
        "filepath": c.file.filepath,
        "code_content": c.code_content
    } for c in chunks]

@app.post("/repos/{repo_id}/chat")
def chat_with_repo(repo_id: int, request: ChatRequest, db: Session = Depends(get_db)):
    # 1. Retrieve the repository
    repo = db.query(Repository).filter(Repository.id == repo_id).first()
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found.")
    
    # 2. Embed user query
    query_vector = get_openai_embedding(request.query)
    
    # 3. Retrieve relevant chunks using cosine similarity via pgvector
    # Lower cosine_distance means higher similarity
    relevant_chunks = db.query(CodeChunk).filter(
        CodeChunk.repository_id == repo_id
    ).order_by(
        CodeChunk.embedding.cosine_distance(query_vector)
    ).limit(5).all()
    
    if not relevant_chunks:
        return {
            "answer": "I couldn't find any relevant code in this repository. Ensure it has been indexed properly.",
            "sources": []
        }
        
    # Format resources for context
    context_blocks = []
    sources = []
    for chunk in relevant_chunks:
        filepath = chunk.file.filepath
        context_blocks.append(
            f"--- File: {filepath} ({chunk.type}: {chunk.name}, Lines {chunk.start_line}-{chunk.end_line}) ---\n"
            f"{chunk.code_content}\n"
        )
        sources.append({
            "filepath": filepath,
            "name": chunk.name,
            "type": chunk.type,
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "code_content": chunk.code_content
        })
        
    context_str = "\n".join(context_blocks)
    
    # 4. Generate LLM Completion
    if not openai_client:
        # Fallback Mock RAG Response
        answer = (
            "⚠️ **OFFLINE MODE: OpenAI API key is mock/not configured.**\n\n"
            "Here is the code I retrieved semantically matching your question:\n\n"
        )
        for idx, src in enumerate(sources):
            answer += f"**{idx+1}. {src['filepath']}** (`{src['type']}: {src['name']}`)\n"
        answer += "\nConfigure a valid `OPENAI_API_KEY` to get full conversational AI explanations."
        return {
            "answer": answer,
            "sources": sources
        }
        
    try:
        system_prompt = (
            "You are GitStage, an advanced AI software engineer chatbot. "
            "Your task is to answer the user's question about the repository codebase using ONLY the retrieved code chunks below. "
            "Be precise, reference files and line numbers directly, and structure code snippets cleanly. "
            "If the provided context does not contain enough information to answer, state that you don't know based on the retrieved files.\n\n"
            f"Code Context:\n{context_str}"
        )
        
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": request.query}
            ],
            temperature=0.2
        )
        
        return {
            "answer": response.choices[0].message.content,
            "sources": sources
        }
    except Exception as e:
        logger.error(f"Chat generation error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate response: {e}")

@app.websocket("/ws/progress/{repo_id}")
async def websocket_progress(websocket: WebSocket, repo_id: int):
    """WebSocket handler streaming real-time ingestion status from Redis or In-Memory PubSub."""
    await websocket.accept()
    logger.info(f"WebSocket client connected for repo {repo_id}")
    
    channel = f"repo_progress_{repo_id}"
    
    # 1. Subscribe to In-Memory PubSub queue
    in_memory_queue = pubsub_manager.subscribe(channel)
    
    # 2. Try subscribing to Redis PubSub if REDIS_URL is active
    redis_async = None
    pubsub = None
    try:
        if REDIS_URL and REDIS_URL != "mock":
            redis_async = aioredis.from_url(REDIS_URL)
            pubsub = redis_async.pubsub()
            await pubsub.subscribe(channel)
    except Exception as e:
        logger.warning(f"Redis PubSub subscription skipped/failed (expected in free-tier): {e}")
        redis_async = None
        pubsub = None

    async def read_redis():
        if not pubsub:
            return
        while True:
            try:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
                if message:
                    data = message["data"].decode("utf-8")
                    await websocket.send_text(data)
                    json_data = json.loads(data)
                    if json_data.get("status") in ["synced", "failed"]:
                        break
                await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in Redis websocket reader: {e}")
                break

    async def read_in_memory():
        while True:
            try:
                data = await in_memory_queue.get()
                await websocket.send_text(data)
                json_data = json.loads(data)
                if json_data.get("status") in ["synced", "failed"]:
                    break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in in-memory websocket reader: {e}")
                break

    # Run both readers concurrently, returning when the first one finishes (synced or failed)
    redis_task = asyncio.create_task(read_redis())
    in_memory_task = asyncio.create_task(read_in_memory())
    
    # Run a ping loop to keep socket alive and detect disconnects
    async def ping_loop():
        while True:
            try:
                await asyncio.sleep(1.0)
                await websocket.send_json({"ping": True})
            except asyncio.CancelledError:
                break
            except Exception:
                # Socket disconnected
                redis_task.cancel()
                in_memory_task.cancel()
                break

    ping_task = asyncio.create_task(ping_loop())

    try:
        await asyncio.wait(
            [redis_task, in_memory_task],
            return_when=asyncio.FIRST_COMPLETED
        )
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket execution error for repo {repo_id}: {e}")
    finally:
        # Cancel all tasks to avoid leaks
        redis_task.cancel()
        in_memory_task.cancel()
        ping_task.cancel()
        
        # Cleanup subscriptions
        pubsub_manager.unsubscribe(channel, in_memory_queue)
        if pubsub:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.close()
            except Exception:
                pass
        if redis_async:
            try:
                await redis_async.close()
            except Exception:
                pass
        logger.info(f"WebSocket client disconnected for repo {repo_id}")


@app.post("/webhooks/github")
async def github_webhook(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Webhook listener to automatically trigger re-indexing on repository git push events."""
    try:
        payload = await request.json()
        github_event = request.headers.get("X-GitHub-Event")
        
        if github_event == "push":
            repo_info = payload.get("repository", {})
            github_repo_id = repo_info.get("id")
            installation_info = payload.get("installation", {})
            installation_id = installation_info.get("id")
            
            if github_repo_id and installation_id:
                # Find matching repository in DB
                repo = db.query(Repository).filter(Repository.github_id == github_repo_id).first()
                if not repo:
                    # Fallback lookup by full name
                    full_name = repo_info.get("full_name")
                    repo = db.query(Repository).filter(Repository.full_name == full_name).first()
                    
                if repo:
                    logger.info(f"GitHub webhook: push event detected for {repo.full_name}. Triggering re-index task.")
                    # Trigger task via FastAPI BackgroundTasks
                    background_tasks.add_task(ingest_github_repository_task, repo.id, installation_id)
                    return {"status": "re_indexing_triggered"}
                    
        return {"status": "ignored", "reason": "Event type not processed or repository not indexed."}
    except Exception as e:
        logger.error(f"Webhook processing error: {e}")
        raise HTTPException(status_code=500, detail="Internal webhook error")


@app.post("/payments/checkout")
def create_stripe_checkout(user: User = Depends(get_current_user)):
    """Creates a Stripe checkout session url, or a mock url if Stripe keys are missing."""
    if not STRIPE_API_KEY or STRIPE_API_KEY == "mock_stripe_key":
        # Dev mock url callback
        mock_checkout_url = f"{FRONTEND_URL}/payments/mock-checkout?session_id=mock_session_123"
        return {"checkout_url": mock_checkout_url}
        
    try:
        checkout_session = stripe.checkout.Session.create(
            line_items=[
                {
                    "price": STRIPE_PRICE_ID,
                    "quantity": 1,
                },
            ],
            mode="subscription",
            success_url=f"{FRONTEND_URL}/dashboard?billing=success&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{FRONTEND_URL}/dashboard?billing=cancelled",
            client_reference_id=str(user.id),
            customer_email=user.email,
        )
        return {"checkout_url": checkout_session.url}
    except Exception as e:
        logger.error(f"Stripe session generation failure: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to initiate Stripe billing: {e}")


@app.post("/payments/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """Webhook listener to process checkout completions and subscription cancellations from Stripe."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    
    if not STRIPE_WEBHOOK_SECRET or STRIPE_WEBHOOK_SECRET == "mock_webhook_secret" or not sig_header:
        # Development mock webhook parser
        try:
            event_data = json.loads(payload.decode("utf-8"))
            event_type = event_data.get("type")
            event_obj = event_data.get("data", {}).get("object", {})
        except Exception as e:
            logger.error(f"Mock webhook json parse error: {e}")
            raise HTTPException(status_code=400, detail="Invalid payload")
    else:
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, STRIPE_WEBHOOK_SECRET
            )
            event_type = event["type"]
            event_obj = event["data"]["object"]
        except stripe.error.SignatureVerificationError as e:
            logger.error(f"Stripe signature verification failed: {e}")
            raise HTTPException(status_code=400, detail="Invalid signature")
        except Exception as e:
            logger.error(f"Stripe webhook parsing failure: {e}")
            raise HTTPException(status_code=400, detail="Webhook error")
            
    # Handle the event
    if event_type == "checkout.session.completed":
        client_ref_id = event_obj.get("client_reference_id")
        stripe_cust_id = event_obj.get("customer")
        if client_ref_id:
            user = db.query(User).filter(User.id == int(client_ref_id)).first()
            if user:
                user.stripe_customer_id = stripe_cust_id
                user.subscription_tier = "pro"
                db.commit()
                logger.info(f"User {user.username} upgraded to PRO tier.")
                
    elif event_type == "customer.subscription.deleted":
        stripe_cust_id = event_obj.get("customer")
        if stripe_cust_id:
            user = db.query(User).filter(User.stripe_customer_id == stripe_cust_id).first()
            if user:
                user.subscription_tier = "free"
                db.commit()
                logger.info(f"User {user.username} downgraded to FREE tier.")
                
    return {"status": "success"}


@app.post("/payments/mock-upgrade")
def mock_upgrade_user(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Developer helper route to immediately upgrade the local session to PRO."""
    user.subscription_tier = "pro"
    db.commit()
    return {"message": "Success", "subscription_tier": user.subscription_tier}


@app.post("/payments/mock-downgrade")
def mock_downgrade_user(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Developer helper route to immediately downgrade the local session to FREE."""
    user.subscription_tier = "free"
    db.commit()
    return {"message": "Success", "subscription_tier": user.subscription_tier}
