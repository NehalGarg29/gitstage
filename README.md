# GitStage 🚀

GitStage is an advanced, AI-powered software assistant that ingests Python codebases, parses their AST structure into semantic code chunks, generates vector embeddings, and allows developer interaction via a robust RAG (Retrieval-Augmented Generation) chatbot.

## 🌐 Live Application
*   **Vercel Production URL**: [https://gitstage.vercel.app](https://gitstage.vercel.app)
*   **Vercel Deployment URL**: [https://gitstage-f18peo1hg-nehalgar-3419s-projects.vercel.app](https://gitstage-f18peo1hg-nehalgar-3419s-projects.vercel.app)

---

## 🛠️ Deployment & Getting Started

### ☁️ Cloud Deployment (Production)
For instructions on deploying the full hybrid cloud production architecture (React Frontend on Vercel + FastAPI Backend on Render/Railway + PostgreSQL with pgvector on Neon/Supabase), refer to the [Cloud Deployment Guide](cloud-deployment.md).

### 🐳 Local Development (Docker Compose)
To run the full stack locally (including the local database, celery workers, Redis, backend, and frontend containers):

1.  Create a `.env` file in the root directory:
    ```env
    OPENAI_API_KEY=your_openai_api_key_here
    ```
2.  Start the services:
    ```bash
    docker compose up --build
    ```
3.  Access the services:
    *   **Frontend**: `http://localhost:5173`
    *   **Backend API**: `http://localhost:8080`
    *   **Postgres Database**: `localhost:5432`
    *   **Redis Cache**: `localhost:6379`
