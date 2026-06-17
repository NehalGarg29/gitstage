# GitStage Production Deployment Guide (Option 1: Single VM)

This guide walks through deploying GitStage on a single cloud Virtual Machine (e.g. AWS EC2 or DigitalOcean Droplet) using Docker Compose and Nginx.

---

## 📋 Prerequisites & Hardware Requirements

*   **Instance Size:** Minimum **4GB RAM** (e.g., AWS `t3.medium` or a DigitalOcean $24/mo Droplet). The vector indexing, Celery worker, and DB search queries run more smoothly with at least 4GB.
*   **Operating System:** Ubuntu 22.04 LTS or Ubuntu 24.04 LTS.
*   **Network (Security Groups):** Ensure the following inbound ports are open:
    *   `22` (SSH) — Restricted to your IP.
    *   `80` (HTTP) — Open to all.
    *   `443` (HTTPS) — Open to all.

---

## 🛠️ Step 1: Install Docker & Docker Compose on Server

Connect to your VM via SSH and run:

```bash
# Update package list
sudo apt-get update && sudo apt-get upgrade -y

# Install prerequisites
sudo apt-get install -y curl git apt-transport-https ca-certificates gnupg lsb-release

# Add Docker’s official GPG key
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg

# Set up repository
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install Docker Engine
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Verify installation
sudo docker run hello-world
```

---

## 📂 Step 2: Clone Codebase & Configure Environment

1. Clone your repository to `/var/www/gitstage`:
   ```bash
   sudo git clone <YOUR_REPO_URL> /var/www/gitstage
   cd /var/www/gitstage
   ```

2. Create a production environment file `.env` in the root folder `/var/www/gitstage/.env`:
   ```bash
   sudo nano .env
   ```

3. Paste and update the following production secrets:
   ```env
   # Database Credentials
   POSTGRES_PASSWORD=your_super_secure_random_db_password

   # OpenAI Credentials
   OPENAI_API_KEY=sk-proj-YourOpenAiKeyHere...

   # GitHub App Integration
   GITHUB_CLIENT_ID=Iv1.YourClientId
   GITHUB_CLIENT_SECRET=YourClientSecret
   GITHUB_REDIRECT_URI=https://gitstage.yourdomain.com/api/auth/github/callback

   # JWT Config
   JWT_SECRET=generate_a_random_jwt_signing_key_32_characters

   # Stripe Credentials
   STRIPE_API_KEY=sk_live_YourStripeApiKey
   STRIPE_WEBHOOK_SECRET=whsec_YourStripeWebhookSecret
   STRIPE_PRICE_ID=price_YourLiveProPriceId

   # Host URLs
   FRONTEND_URL=https://gitstage.yourdomain.com
   VITE_API_URL=https://gitstage.yourdomain.com/api
   ```

---

## 🚀 Step 3: Run the Production Stack

Build the containers and launch them in the background (detached daemon mode):

```bash
# Build production images and pull dependencies
sudo docker compose -f docker-compose.prod.yml build

# Launch the services
sudo docker compose -f docker-compose.prod.yml up -d
```

Check that all services boot cleanly:
```bash
sudo docker compose -f docker-compose.prod.yml ps
sudo docker compose -f docker-compose.prod.yml logs frontend
```

---

## 🔒 Step 4: Configure SSL Certificates (HTTPS)

You can choose one of the two standard SSL strategies:

### Option A: Cloudflare SSL Proxy (Easiest)
1. Point your domain (e.g. `gitstage.yourdomain.com`) to your server's public IP address in Cloudflare DNS.
2. Toggle the **Proxy Status** to **Proxied** (orange cloud).
3. In the Cloudflare Dashboard, navigate to **SSL/TLS** and set the encryption mode to **Flexible**.
4. Cloudflare will automatically handle the SSL handshake and serve your app securely over HTTPS.

### Option B: Certbot & Host Reverse Proxy (Let's Encrypt)
If you prefer managing SSL certificates directly on the host instance:

1. Update `/var/www/gitstage/docker-compose.prod.yml` to run the frontend container on a custom internal port instead of port 80:
   ```yaml
   # Change frontend container ports to avoid port 80 conflict on the host
   ports:
     - "127.0.0.1:8080:80"
   ```

2. Install **Nginx** and **Certbot** on the host server:
   ```bash
   sudo apt-get install -y nginx certbot python3-certbot-nginx
   ```

3. Configure Nginx on the host to forward to port `8080`:
   ```bash
   sudo nano /etc/nginx/sites-available/gitstage
   ```
   Paste the following server block:
   ```nginx
   server {
       listen 80;
       server_name gitstage.yourdomain.com;

       location / {
           proxy_pass http://127.0.0.1:8080;
           proxy_http_version 1.1;
           proxy_set_header Upgrade $http_upgrade;
           proxy_set_header Connection "upgrade";
           proxy_set_header Host $host;
           proxy_cache_bypass $http_upgrade;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
       }
   }
   ```

4. Enable the config and obtain the SSL certificate:
   ```bash
   sudo ln -s /etc/nginx/sites-available/gitstage /etc/nginx/sites-enabled/
   sudo nginx -t
   sudo systemctl restart nginx
   
   # Run Certbot to acquire SSL certificate
   sudo certbot --nginx -d gitstage.yourdomain.com
   ```
   Certbot will automatically verify ownership, fetch the certificate, and update Nginx to route all traffic securely over port `443`.
