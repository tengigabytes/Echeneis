# Deployment Guide

## Prerequisites

- Docker and Docker Compose
- API keys for at least one supported provider

## Environment Variables

Copy `.env.example` to `.env` and fill in your API keys:

```bash
cp .env.example .env
```

### Required Variables

| Variable | Description |
|----------|-------------|
| `LITELLM_MASTER_KEY` | Master key for LiteLLM proxy authentication |
| `GOOGLE_AI_STUDIO_API_KEY` | Google AI Studio API key |
| `MISTRAL_API_KEY` | Mistral API key |
| `CEREBRAS_API_KEY` | Cerebras API key |
| `GROQ_API_KEY` | Groq API key |
| `CLOUDFLARE_API_TOKEN` | Cloudflare Workers AI API token |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare account ID |
| `GITHUB_TOKEN` | GitHub personal access token (for GitHub Models) |
| `OPENROUTER_API_KEY` | OpenRouter API key |
| `GEMINI_API_KEY` | Google Gemini API key |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token (for bot functionality) |

Not all keys are required. The gateway will use whichever providers have valid keys configured.

## Running with Docker Compose

```bash
docker-compose up -d
```

The gateway will be available at `http://localhost:4000`.

### Verify

```bash
curl http://localhost:4000/health
```

## ARM Deployment (Oracle Cloud Free Tier)

The LiteLLM Docker image supports ARM64. No additional configuration is needed for ARM-based instances.

```bash
# SSH into your Oracle Cloud ARM instance
ssh ubuntu@<instance-ip>

# Clone and start
git clone https://github.com/tengigabytes/Echeneis.git
cd Echeneis
cp .env.example .env
# Edit .env with your keys
docker-compose up -d
```
