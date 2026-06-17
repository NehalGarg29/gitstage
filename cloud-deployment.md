# GitStage Cloud Deployment Guide

This guide walks through deploying the GitStage application using a hybrid production model:
1.  **Frontend**: Deployed to **Vercel** (Vite + React)
2.  **Backend**: Deployed to **Render** or **Railway** (FastAPI with WebSockets and background tasks)
3.  **Database**: Deployed to **Neon** or **Supabase** (Postgres with `pgvector`)

---

## 🗄️ Step 1: Database Setup (Neon or Supabase)

GitStage requires a PostgreSQL database with the `pgvector` extension enabled.

1.  Sign up or log in to **[Neon.tech](https://neon.tech/)** (or Supabase).
2.  Create a new project.
3.  Copy the connection string (Connection URI) under your dashboard. It will look like:
    ```
    postgresql://nehalgarg:PASSWORD@ep-random-string.us-east-2.aws.neon.tech/neondb?sslmode=require
    ```
    *Keep this database URL handy for the backend configuration.*

---

## 🚀 Step 2: Backend API Deployment (Render or Railway)

Since GitStage runs background indexing tasks (via FastAPI `BackgroundTasks`) and sends progress notifications via WebSockets, a persistent server hosting option like **Render** or **Railway** is required.

### Option A: Render Setup
1.  Sign up/log in to **[Render](https://render.com/)**.
2.  Click **New +** and select **Web Service**.
3.  Connect your GitHub repository containing GitStage.
4.  Configure the service settings:
    *   **Name**: `gitstage-backend`
    *   **Root Directory**: `backend`
    *   **Runtime**: `Python 3`
    *   **Build Command**: `pip install -r requirements.txt`
    *   **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
5.  Add the following **Environment Variables** in the service configuration:
    *   `DATABASE_URL`: *Your Neon/Supabase PostgreSQL connection string (from Step 1)*
    *   `OPENAI_API_KEY`: *Your OpenAI API Key*
    *   `JWT_SECRET`: *A secure random string (e.g. 32+ characters)*
    *   `FRONTEND_URL`: *The URL of your Vercel deployment (e.g. `https://gitstage.vercel.app` — you can update this after Step 3)*
    *   `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET`: *GitHub OAuth App credentials (if integrating)*
    *   `GITHUB_REDIRECT_URI`: `https://your-backend-url.onrender.com/auth/github/callback`
6.  Click **Deploy Web Service**. Render will build the container and start the API.

---

## 🎨 Step 3: Frontend Deployment (Vercel)

Vercel is the ideal and easiest platform to deploy the static React frontend.

### Option A: Deploy via Vercel Dashboard (GitHub integration)
1.  Sign up/log in to **[Vercel](https://vercel.com/)**.
2.  Click **Add New...** -> **Project**.
3.  Import your GitStage GitHub repository.
4.  Configure the project details:
    *   **Framework Preset**: `Vite`
    *   **Root Directory**: `frontend`
    *   **Build Command**: `npm run build`
    *   **Output Directory**: `dist`
5.  Expand **Environment Variables** and add:
    *   **Key**: `VITE_API_URL`
    *   **Value**: *Your Render or Railway backend URL* (e.g. `https://gitstage-backend.onrender.com` — *Note: Do not append a trailing slash*)
6.  Click **Deploy**.

### Option B: Deploy via Vercel CLI (Local deployment)
If you have the Vercel CLI installed on your machine:
```bash
cd frontend
vercel login
vercel
# Follow the prompt instructions. Ensure you add VITE_API_URL as an environment variable when prompted or via the dashboard.
```

---

## 🛠️ Configuration Checklist

### GitHub OAuth Setup (Optional, for GitHub sign-in)
If you want to support logging in with GitHub:
1.  Go to your GitHub account -> **Settings** -> **Developer Settings** -> **OAuth Apps** -> **New OAuth App**.
2.  Set **Homepage URL** to your Vercel frontend URL.
3.  Set **Authorization callback URL** to `https://<your-backend-url>/auth/github/callback`.
4.  Generate a **Client Secret**.
5.  Set `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET` in your backend environment variables (Render/Railway).

### Real-Time Updates
Because the backend uses an in-memory PubSub system as a fallback when Redis is absent, real-time WebSocket progress updates for repository parsing will work seamlessly as long as the backend runs on a single container/web service node.
