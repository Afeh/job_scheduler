# Production Deployment Guide (Oracle Cloud Free Tier)

This guide walks you through deploying the Dilamme Job Scheduler on an **Oracle Cloud Infrastructure (OCI) free tier instance** with Nginx reverse proxy and HTTPS via Let's Encrypt.

## Table of Contents

- [1. Create Your Oracle Cloud Instance](#1-create-your-oracle-cloud-instance)
- [2. Configure Firewall (Security Lists)](#2-configure-firewall-security-lists)
- [3. Connect to Your Instance](#3-connect-to-your-instance)
- [4. Install Dependencies on the Server](#4-install-dependencies-on-the-server)
- [5. Set Up a Domain (Dynamic DNS)](#5-set-up-a-domain-dynamic-dns)
- [6. Deploy the Application Code](#6-deploy-the-application-code)
- [7. Build the Frontend](#7-build-the-frontend)
- [8. Configure Nginx as a Reverse Proxy](#8-configure-nginx-as-a-reverse-proxy)
- [9. Enable HTTPS with Let's Encrypt](#9-enable-https-with-lets-encrypt)
- [10. Set Up Systemd Services (Auto-start)](#10-set-up-systemd-services-auto-start)
- [11. Verify the Deployment](#11-verify-the-deployment)
- [12. Production Hardening Checklist](#12-production-hardening-checklist)

---

## 1. Create Your Oracle Cloud Instance

1. Sign in to [cloud.oracle.com](https://cloud.oracle.com).
2. Navigate to **Compute → Instances**.
3. Click **Create Instance**.
4. **Name**: `job-scheduler` (or anything you like).
5. **Placement**: Keep defaults (Oracle recommends the always-free eligible AD).
6. **Image**: Select **Canonical Ubuntu 22.04** (or 24.04) — this guide assumes Ubuntu.
7. **Shape**: Select **VM.Standard.A1.Flex** (Ampere ARM, 4 OCPUs, 24 GB RAM — always free) or **VM.Standard.E2.1.Micro** (AMD, 1 OCPU, 1 GB RAM — always free).
   > The A1.Flex ARM instance is more powerful and is always-free eligible in most regions.
8. **Add SSH keys**: Upload your public SSH key or generate a new key pair.
9. **Boot volume**: Default (50 GB always free) is plenty.
10. Click **Create**.

Wait ~2 minutes for the instance to provision. Note the **Public IP Address**.

---

## 2. Configure Firewall (Security Lists)

By default, Oracle's security lists block most inbound traffic. You need to open HTTP (80) and HTTPS (443).

1. Go to **Networking → Virtual Cloud Networks**.
2. Click your VCN (usually named after your compartment).
3. Click the **Security List** that's attached to your subnet.
4. Click **Add Ingress Rules** and add:

| Source Type | Source CIDR | IP Protocol | Destination Port | Description |
|------------|------------|------------|-----------------|-------------|
| CIDR | `0.0.0.0/0` | TCP | `80` | HTTP |
| CIDR | `0.0.0.0/0` | TCP | `443` | HTTPS |
| CIDR | `0.0.0.0/0` | TCP | `22` | SSH (should already exist) |

5. Click **Add Ingress Rules**.

---

## 3. Connect to Your Instance

```bash
# From your local machine
ssh -i ~/.ssh/your-key.pem ubuntu@<PUBLIC_IP_ADDRESS>
```

If you used Ubuntu as the OS image, the default username is `ubuntu`.

---

## 4. Install Dependencies on the Server

Run these commands on the server:

```bash
# Update packages
sudo apt update && sudo apt upgrade -y

# Install Python 3, pip, venv
sudo apt install -y python3 python3-pip python3-venv

# Install Node.js 20.x (for building the frontend)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

# Install Nginx
sudo apt install -y nginx

# Verify installations
python3 --version   # Should be 3.10+
node --version      # Should be v20.x
npm --version
nginx -v
```

---

## 5. Set Up a Domain (Dynamic DNS)

Since Oracle Cloud free tier instances typically have a public IP that can change if you stop/start the instance, you have two options:


### Option A: No-IP (Free Dynamic DNS)
1. Go to [noip.com](https://www.noip.com) and create a free account.
2. Choose a hostname (e.g., `my-scheduler.ddns.net`).
3. Install the No-IP DUC (Dynamic Update Client) on your server:
   ```bash
   sudo apt install -y make gcc
   cd /tmp
   wget https://www.noip.com/client/linux/noip-duc-linux.tar.gz
   tar xf noip-duc-linux.tar.gz
   cd noip-*/
   make
   sudo make install
   sudo /usr/local/bin/noip2 -C   # Configure with your No-IP credentials '/usr/local/etc/no-ip2.conf'
   ```
4. The DUC will automatically keep your domain pointed at your server's IP.

### Option B: DuckDNS (Free, Simpler)
1. Go to [duckdns.org](https://duckdns.org) and sign in with any account.
2. Create a subdomain (e.g., `my-scheduler.duckdns.org`).
3. Set up a cron job to update the IP:
   ```bash
   # Create a script
   mkdir -p ~/scripts
   cat > ~/scripts/duckdns.sh << 'EOF'
   echo url="https://www.duckdns.org/update?domains=YOUR_DOMAIN&token=YOUR_TOKEN&ip=" | curl -k -o ~/duckdns.log -K -
   EOF
   chmod +x ~/scripts/duckdns.sh

   # Add to crontab
   echo "*/5 * * * * ~/scripts/duckdns.sh" | crontab -
   ```

After this, you'll have a domain like `my-scheduler.duckdns.org` pointing at your server.

---

## 6. Deploy the Application Code

On the server:

```bash
# Clone the repository
cd ~
git clone <your-repo-url> job_scheduler
cd job_scheduler

# Set up Python virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install Python dependencies
pip install -r backend/requirements.txt

# Create backend environment configuration
cp backend/.env.example backend/.env
# Edit the .env file with your production values:
nano backend/.env
```

**Edit `backend/.env`** with these values:
```ini
# .env - Production Settings
CORS_ORIGINS=https://my-scheduler.duckdns.org
LOG_LEVEL=INFO
```

---

## 7. Build the Frontend

```bash
cd ~/job_scheduler/frontend

# Install dependencies
npm install

# Build for production
npm run build

# The built files are now in frontend/dist/
ls dist/
```

---

## 8. Configure Nginx as a Reverse Proxy

The deployment includes a template at `deploy/nginx.conf`. Here's how to set it up:

```bash
cd ~/job_scheduler

# Copy and edit the Nginx configuration
sudo cp deploy/nginx.conf /etc/nginx/sites-available/job-scheduler

# Edit the configuration
sudo nano /etc/nginx/sites-available/job-scheduler
```

**Replace these placeholders** in the config file:
- `YOUR_DOMAIN` → your dynamic DNS domain (e.g., `my-scheduler.duckdns.org`)
- `FRONTEND_DIST_PATH` → `/home/ubuntu/job_scheduler/frontend/dist`
- `BACKEND_PORT` → `8000`

**Enable the site and reload Nginx**:

```bash
sudo ln -s /etc/nginx/sites-available/job-scheduler /etc/nginx/sites-enabled/

# Remove the default site (optional)
sudo rm /etc/nginx/sites-enabled/default

# Test configuration syntax
sudo nginx -t

# Reload Nginx
sudo systemctl reload nginx
```

---

## 9. Enable HTTPS with Let's Encrypt

Install Certbot and obtain an SSL certificate:

```bash
# Install Certbot for Nginx
sudo apt install -y certbot python3-certbot-nginx

# Obtain and install certificate (this auto-configures Nginx)
sudo certbot --nginx -d my-scheduler.duckdns.org

# Follow the prompts:
# - Enter your email (for renewal notices)
# - Agree to terms
# - Choose whether to redirect HTTP to HTTPS (recommended: yes)
```

**Auto-renewal**: Certbot installs a systemd timer and cron job for automatic renewal. Verify:

```bash
sudo systemctl status certbot.timer
# Test the renewal process:
sudo certbot renew --dry-run
```

---

## 10. Set Up Systemd Services (Auto-start)

The deployment includes systemd service files in `deploy/systemd/`.

### API Service

```bash
sudo cp ~/job_scheduler/deploy/systemd/api.service /etc/systemd/system/scheduler-api.service

# Edit the file to match your paths:
sudo nano /etc/systemd/system/scheduler-api.service
```

Ensure the paths match your setup:
- `User=ubuntu`
- `WorkingDirectory=/home/ubuntu/job_scheduler`
- `EnvironmentFile=/home/ubuntu/job_scheduler/backend/.env`
- `ExecStart=/home/ubuntu/job_scheduler/.venv/bin/uvicorn backend.api:app --host 0.0.0.0 --port 8000`

### Worker Service

```bash
sudo cp ~/job_scheduler/deploy/systemd/worker.service /etc/systemd/system/scheduler-worker.service

# Edit if needed:
sudo nano /etc/systemd/system/scheduler-worker.service
```

### Enable and Start Services

```bash
sudo systemctl daemon-reload

# Enable services to start on boot
sudo systemctl enable scheduler-api.service
sudo systemctl enable scheduler-worker.service

# Start the API first, then the worker
sudo systemctl start scheduler-api.service
sudo systemctl start scheduler-worker.service

# Check status
sudo systemctl status scheduler-api.service
sudo systemctl status scheduler-worker.service
```

### Useful Systemd Commands

```bash
# Check logs
sudo journalctl -u scheduler-api.service -f   # Follow API logs
sudo journalctl -u scheduler-worker.service -f # Follow worker logs

# Restart services
sudo systemctl restart scheduler-api.service
sudo systemctl restart scheduler-worker.service
```

---

## 11. Verify the Deployment

From your browser, visit: **`https://my-scheduler.duckdns.org`** (replace with your domain)

You should see the Dilamme Scheduler dashboard.

**API health check** (from your local machine):
```bash
curl https://my-scheduler.duckdns.org/jobs
# Should return an empty array or your jobs: []
```

To test job creation:
```bash
curl -X POST https://my-scheduler.duckdns.org/jobs \
  -H "Content-Type: application/json" \
  -d '{"type": "send_email", "priority": 2, "payload": "{\"to\":\"test@example.com\"}"}'
```

---

## 12. Production Hardening Checklist

- [ ] **Use a PostgreSQL database** (instead of SQLite) for better concurrency and reliability. Oracle Cloud Free Tier includes two AMD OCPU-based Autonomous Databases:
  - Set `DATABASE_URL` in `.env` to your PostgreSQL connection string.
  - Install `psycopg2-binary` or `asyncpg`: `pip install psycopg2-binary`
- [ ] **Restrict SSH access**: Use key-based auth only, disable password auth.
- [ ] **Enable UFW firewall**: `sudo ufw allow 22/tcp && sudo ufw allow 443/tcp && sudo ufw enable`
- [ ] **Set up automated backups** for your database to Object Storage.
- [ ] **Monitor resource usage**: `htop`, `df -h`, and consider setting up alerts.
- [ ] **Configure log rotation**: Nginx and systemd logs can grow large over time.
- [ ] **Update regularly**: `sudo apt update && sudo apt upgrade -y` (set up unattended-upgrades).
- [ ] **Use a production ASGI server**: For higher traffic, replace `uvicorn` with `gunicorn -k uvicorn.workers.UvicornWorker`.
- [ ] **Run the worker in a separate screen/tmux session** if you don't want to use systemd.

---

## Files in `deploy/`

| File | Purpose |
|------|---------|
| `deploy/nginx.conf` | Nginx reverse proxy configuration template |
| `deploy/systemd/api.service` | Systemd unit for the FastAPI backend |
| `deploy/systemd/worker.service` | Systemd unit for the background worker |
| `backend/.env.example` | Environment variable template |
| `backend/requirements.txt` | Python dependencies |

---

## Troubleshooting

**502 Bad Gateway from Nginx**
- The backend isn't running: `sudo systemctl restart scheduler-api.service`
- Check logs: `sudo journalctl -u scheduler-api.service -n 50`

**CORS errors in browser**
- Ensure `CORS_ORIGINS` in `backend/.env` matches your frontend domain exactly (with `https://`).
- Restart the API after changing `.env`.

**Worker not processing jobs**
- Check if the worker is running: `sudo systemctl status scheduler-worker.service`
- Verify the worker can reach the API: check `API_URL` in `.env`.

**"No module named backend"**
- Ensure you're running from the project root (`/home/ubuntu/job_scheduler`).
- Activate the virtual environment: `source .venv/bin/activate`.

**Port already in use**
- Check what's using port 80/443: `sudo lsof -i :80`
- Restart Nginx: `sudo systemctl restart nginx`
