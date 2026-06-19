#!/usr/bin/env bash
# JobPilot — interactive .env setup (Linux / macOS).
#
# Creates a ready-to-run .env: pick your AI provider, answer a couple of
# prompts, and you get a valid config + a persistent credential-encryption key.
# Works for both Docker and local-dev installs — no toolchain required beyond
# bash + openssl (or python3 as a fallback for key generation).
#
#   bash scripts/setup.sh
#   docker compose up -d --build      # then this
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$ROOT/.env"
EXAMPLE="$ROOT/.env.example"

say()  { printf '%s\n' "$*"; }
ask()  { local p="$1" d="${2:-}" r; if [ -n "$d" ]; then read -r -p "$p [$d]: " r || true; printf '%s' "${r:-$d}"; else read -r -p "$p: " r || true; printf '%s' "$r"; fi; }

# Replace or append KEY=VALUE in .env (position-independent; comments kept).
set_env() {
  local key="$1" val="$2"
  if [ -f "$ENV_FILE" ]; then
    grep -v "^${key}=" "$ENV_FILE" > "$ENV_FILE.tmp" 2>/dev/null || true
    mv "$ENV_FILE.tmp" "$ENV_FILE"
  fi
  printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
}

gen_fernet_key() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 32 | tr '+/' '-_'
  elif command -v python3 >/dev/null 2>&1; then
    python3 - <<'PY'
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
PY
  else
    say "ERROR: need openssl or python3 to generate CREDENTIAL_KEY." >&2
    exit 1
  fi
}

say "──────────────────────────────────────────────"
say " JobPilot setup"
say "──────────────────────────────────────────────"

if [ -f "$ENV_FILE" ]; then
  ow="$(ask "An .env already exists. Overwrite it? (y/N)" "N")"
  case "$ow" in y|Y|yes|YES) : ;; *) say "Keeping existing .env. Nothing changed."; exit 0 ;; esac
fi
cp "$EXAMPLE" "$ENV_FILE"

# Clear the example's placeholder credentials/routing so the result only
# contains what you actually choose below (placeholders otherwise read as
# real, non-empty values).
for _k in ADZUNA_APP_ID ADZUNA_APP_KEY OPENAI_API_KEY ANTHROPIC_API_KEY \
          SERPAPI_KEY LLM_API_KEY LLM_BASE_URL LLM_MODEL EMBEDDING_API_KEY EMBEDDING_BASE_URL \
          BROWSER_LLM_API_KEY BROWSER_LLM_BASE_URL BROWSER_LLM_MODEL; do
  set_env "$_k" ""
done

say ""
say "Which AI provider should JobPilot use?"
say "  1) Local / self-hosted  (OpenAI-compatible: Ollama, llama.cpp, LM Studio, vLLM)"
say "  2) OpenAI-compatible    (cloud; hosted OpenAI or any OpenAI-compatible endpoint)"
say "  3) Anthropic            (cloud; generation only — see notes)"
choice="$(ask "Choice" "1")"

case "$choice" in
  1)
    say ""
    say "Tip: from inside Docker, a model on THIS machine is reachable as"
    say "     http://host.docker.internal:<port>/v1  (not http://localhost)."
    base="$(ask "Model base URL" "http://host.docker.internal:11434/v1")"
    model="$(ask "Model name (as the server reports it)" "")"
    set_env LLM_PROVIDER openai
    set_env LLM_BASE_URL "$base"
    [ -n "$model" ] && set_env LLM_MODEL "$model"
    set_env BROWSER_LLM_PROVIDER openai
    set_env BROWSER_LLM_BASE_URL "$base"
    [ -n "$model" ] && set_env BROWSER_LLM_MODEL "$model"
    say ""
    say "Embeddings power job/CV fit-scoring. Most local servers do NOT serve"
    say "embeddings, so you can point them at a hosted OpenAI-compatible key or"
    say "skip scoring."
    ek="$(ask "API key for embeddings (blank = use the local server)" "")"
    if [ -n "$ek" ]; then
      set_env EMBEDDING_PROVIDER openai; set_env EMBEDDING_API_KEY "$ek"
    else
      # No cloud key: point embeddings at the same local server. Config stays
      # valid and the app boots; if the server doesn't serve /v1/embeddings,
      # fit-scoring degrades at runtime instead of blocking startup.
      set_env EMBEDDING_PROVIDER openai; set_env EMBEDDING_BASE_URL "$base"
      say "  (embeddings will use $base — fit-scoring needs an embeddings-capable server)"
    fi
    ;;
  2)
    ok="$(ask "API key (sk-… for hosted OpenAI)" "")"
    burl="$(ask "Base URL (blank = hosted OpenAI; set for another OpenAI-compatible endpoint)" "")"
    set_env LLM_PROVIDER openai
    set_env EMBEDDING_PROVIDER openai
    set_env BROWSER_LLM_PROVIDER openai
    set_env OPENAI_API_KEY "$ok"
    if [ -n "$burl" ]; then
      set_env LLM_BASE_URL "$burl"
      set_env EMBEDDING_BASE_URL "$burl"
      set_env BROWSER_LLM_BASE_URL "$burl"
    fi
    ;;
  3)
    ak="$(ask "Anthropic API key (sk-ant-…)" "")"
    set_env LLM_PROVIDER anthropic
    set_env ANTHROPIC_API_KEY "$ak"
    say ""
    say "Note: Anthropic has no embeddings API and browser-use ships no Anthropic"
    say "client, so embeddings + the browser agent fall back to an OpenAI-compatible"
    say "endpoint."
    ek="$(ask "OpenAI-compatible API key for embeddings + browser agent" "")"
    set_env EMBEDDING_PROVIDER openai
    set_env BROWSER_LLM_PROVIDER openai
    [ -n "$ek" ] && { set_env OPENAI_API_KEY "$ek"; }
    ;;
  *) say "Unknown choice — re-run and pick 1-3."; exit 1 ;;
esac

say ""
say "Adzuna powers the job-search source (developer.adzuna.com, free). Optional."
aid="$(ask "ADZUNA_APP_ID (blank to skip)" "")"
if [ -n "$aid" ]; then
  akey="$(ask "ADZUNA_APP_KEY" "")"
  set_env ADZUNA_APP_ID "$aid"
  set_env ADZUNA_APP_KEY "$akey"
fi

# Persistent credential-encryption key (critical for Docker: env_file is not
# writable from inside the container, so a generated-at-runtime key would be
# lost on every restart, breaking stored-credential decryption).
set_env CREDENTIAL_KEY "$(gen_fernet_key)"

say ""
say "✓ Wrote $ENV_FILE"
say ""
say "Next:"
say "  Docker:    docker compose up -d --build   →  http://localhost:8000"
say "  Local dev: uv run python start.py"
