# Deployment Guide

This guide covers deploying Echeneis to an Oracle Cloud Free Tier ARM VM
with Cloudflare Tunnel for secure public access.

## Prerequisites

- Oracle Cloud account (Always Free tier)
- Cloudflare account (free plan)
- API keys for at least one supported LLM provider
- SSH client

## 1. Create Oracle Cloud ARM Instance

1. Log in to Oracle Cloud Console
2. **Compute > Instances > Create Instance**
3. Configure:
   - **Image**: Ubuntu 24.04 (aarch64)
   - **Shape**: VM.Standard.A1.Flex — 4 OCPU, 24 GB RAM
   - **Boot volume**: 200 GB
   - **Networking**: Create new VCN, public subnet, assign public IP
4. **Security List**: Allow ingress on port **22 (SSH)** only
   - Cloudflare Tunnel handles all HTTP traffic — no ports 80/443 needed
5. Download the SSH key pair
6. Note the public IP address

## 2. Initialize the VM

SSH into the instance and run the bootstrap script:

```bash
ssh -i <key-file> ubuntu@<instance-ip>

# Download and run the init script
git clone https://github.com/tengigabytes/Echeneis.git /tmp/echeneis-setup
sudo bash /tmp/echeneis-setup/deploy/vm-init.sh
```

This installs Docker CE, Docker Compose, ufw, fail2ban, stress-ng,
cloudflared, and configures 4 GB swap. The repo is cloned to `/opt/echeneis`.

Log out and back in for the docker group to take effect.

### Optional: Set timezone

```bash
sudo bash /tmp/echeneis-setup/deploy/vm-init.sh --timezone Asia/Singapore
```

Default timezone is `Asia/Tokyo`.

## 3. Configure Environment

```bash
cd /opt/echeneis
cp .env.example .env
nano .env  # Add your API keys
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `LITELLM_MASTER_KEY` | Master key for LiteLLM proxy |
| `GOOGLE_AI_STUDIO_API_KEY` | Google AI Studio API key |
| `MISTRAL_API_KEY` | Mistral API key |
| `CEREBRAS_API_KEY` | Cerebras API key |
| `GROQ_API_KEY` | Groq API key |
| `CLOUDFLARE_API_TOKEN` | Cloudflare Workers AI token |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare account ID |
| `GITHUB_TOKEN` | GitHub PAT (for GitHub Models) |
| `OPENROUTER_API_KEY` | OpenRouter API key |
| `GEMINI_API_KEY` | Google Gemini API key |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_ALLOWED_USERS` | Comma-separated Telegram user IDs |
| `TELEGRAM_ADMIN_CHAT_ID` | (Optional) Separate chat for system alerts |

Not all keys are required. The gateway adapts to whichever providers
have valid keys configured.

## 4. Set Up Cloudflare Tunnel

Cloudflare Tunnel provides secure HTTPS access without exposing any ports.

1. Log in to [Cloudflare Zero Trust](https://one.dash.cloudflare.com/)
2. **Networks > Tunnels > Create a tunnel**
3. Name the tunnel (e.g., `echeneis`)
4. Copy the tunnel token
5. Add a **Public Hostname**:
   - Subdomain: `echeneis` (or your choice)
   - Domain: your Cloudflare domain
   - Service: `http://localhost:4000`

Install the tunnel on the VM:

```bash
sudo cloudflared service install <YOUR_TUNNEL_TOKEN>
```

This creates a systemd service that auto-starts on boot.

## 5. Install and Start

```bash
cd /opt/echeneis
sudo bash deploy/install.sh
sudo systemctl enable --now echeneis
```

This installs the systemd service and the anti-eviction cron job.

## 6. Verify

```bash
# Local check
curl http://localhost:4000/health

# Remote check (via Cloudflare Tunnel)
curl https://echeneis.yourdomain.com/health

# Service status
sudo systemctl status echeneis

# View logs
sudo docker compose -f /opt/echeneis/docker-compose.yml logs -f
```

## 7. Monitoring

### Built-in Telegram Monitoring

The bot includes a background system monitor and on-demand dashboard.

**Proactive alerts** are sent automatically to the admin chat
(or the first allowed user if `TELEGRAM_ADMIN_CHAT_ID` is not set):

| Alert | Trigger |
|-------|---------|
| 🟢 Bot startup | Container starts and gateway is healthy |
| 🔴 CPU / RAM / Disk | CPU > 80%, RAM > 90%, or Disk > 85% |
| 💀 Quota exhaustion | Any model's RPD usage ≥ 90% |
| ⚠️ Circuit breaker | Provider fails 3× consecutively (and recovery) |

Alerts have a 1-hour cooldown per category to prevent flooding.

**On-demand dashboard** — send `/status` in Telegram:

```
🖥 VM 狀態
🟢 CPU  ████░░░░░░ 12.3%  (load 0.49, 4 cores)
🟢 RAM  ███░░░░░░░  8.2/24 GB
🟢 Disk ██░░░░░░░░   45/200 GB

🌐 Gateway
✅ gemma-4-31b
✅ groq-llama-70b

📊 配額
🟢 groq-llama-70b     120/1000  (12%)
🟢 gemma-4-31b         45/1500  (3%)

⏱ 運行 3d 14h 22m
```

### External Uptime Checks (optional)

#### Cloudflare Health Checks

1. Cloudflare Dashboard > your domain > **Health Checks**
2. Create check:
   - URL: `https://echeneis.yourdomain.com/health`
   - Interval: 60 seconds
   - Notification: email or webhook

#### UptimeRobot (free tier)

1. Create account at [UptimeRobot](https://uptimerobot.com/)
2. Add HTTP(S) monitor for your tunnel URL's `/health` endpoint

## 8. Updating

Pull latest code and restart:

```bash
sudo bash /opt/echeneis/deploy/update.sh
```

This runs `git pull`, rebuilds Docker images, and restarts the service.

### Auto-update

A cron job (`deploy/echeneis-auto-update.cron`) polls `origin/main` every
3 minutes. When a new commit is detected with green CI, it triggers
`update.sh` automatically.

The auto-update is **task-aware**: before restarting the service it checks
`/var/lib/echeneis/state/active_tasks.json` for running long-lived tasks
(benchmarks, etc.). If a task is active, the deploy is deferred to the
next cron cycle. Tasks carry a `max_minutes` guard — if the bot crashes
mid-task, the stale entry expires automatically so deploys are never
blocked indefinitely.

```bash
# Check active tasks
cat /var/lib/echeneis/state/active_tasks.json

# View auto-update log
tail -20 /var/log/echeneis-deploy.log
```

## 9. Anti-Eviction

Oracle Cloud Free Tier reclaims idle instances when CPU usage drops below
~20% (95th percentile over 7 days). The `install.sh` script sets up a
cron job that runs `stress-ng` on all 4 CPUs for 30 seconds every 4 hours.

Check it's running:

```bash
# View cron job
cat /etc/cron.d/echeneis-anti-eviction

# Check recent runs in syslog
journalctl -t anti-eviction --since "8 hours ago"
```

## 10. Running Benchmarks

The benchmark suite tests all configured models across 7 dimensions
(latency, context recall, vision, code review, translation, multi-turn,
rate limit stress). Results are saved to `benchmarks/results/results.jsonl`.

### From Telegram

```
/bench                              # all dimensions × all models
/bench latency                      # single dimension
/bench latency groq-llama-70b       # specific dimension + model
/bench all groq-llama-70b           # all dimensions, one model
```

Progress is reported in real-time during the run, and the final report
is pushed when complete.

### From the server

```bash
cd /opt/echeneis
PYTHONPATH=. python -m benchmarks run                             # all
PYTHONPATH=. python -m benchmarks run --dimension latency         # single
PYTHONPATH=. python -m benchmarks results --compare-last 2        # compare runs
```

A full run uses approximately 308 API requests across all providers.

## Troubleshooting

### Service won't start

```bash
sudo systemctl status echeneis
sudo docker compose -f /opt/echeneis/docker-compose.yml logs
```

Common causes:
- Missing `.env` file or invalid API keys
- Docker not running: `sudo systemctl start docker`
- Port conflict: `sudo ss -tlnp | grep 4000`

### ARM build issues

All dependencies have ARM64 wheels. If a pip package fails to install,
check if it requires compilation and install build tools:

```bash
sudo apt install -y build-essential python3-dev
```

### Tunnel disconnects

Cloudflare Tunnel reconnects automatically. If persistent:

```bash
sudo systemctl restart cloudflared
sudo journalctl -u cloudflared -f
```

### High memory usage

The gateway and bot together use ~500 MB. If memory pressure occurs:

```bash
# Check usage
docker stats --no-stream

# Restart to free leaked memory
sudo systemctl restart echeneis
```

### Disk space

Docker logs are capped at 30 MB per service (10m x 3 files).
For Docker image cleanup:

```bash
docker system prune -f
```
