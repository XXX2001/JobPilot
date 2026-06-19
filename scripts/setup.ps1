<#
  JobPilot - interactive .env setup (Windows / PowerShell).

  Creates a ready-to-run .env: pick your AI provider, answer a couple of
  prompts, and you get a valid config + a persistent credential-encryption key.
  Built for the Docker Desktop path - no Python/uv/Node required.

      .\scripts\setup.ps1
      docker compose up -d --build      # then this

  If PowerShell blocks the script, run once:
      Set-ExecutionPolicy -Scope Process Bypass
#>
$ErrorActionPreference = 'Stop'

$Root     = Split-Path -Parent $PSScriptRoot
$EnvFile  = Join-Path $Root '.env'
$Example  = Join-Path $Root '.env.example'

function Ask([string]$Prompt, [string]$Default = '') {
  $suffix = if ($Default) { " [$Default]" } else { '' }
  $r = Read-Host "$Prompt$suffix"
  if ([string]::IsNullOrWhiteSpace($r)) { return $Default }
  return $r
}

# Replace or append KEY=VALUE in .env (position-independent; comments kept).
function Set-Env([string]$Key, [string]$Value) {
  $lines = @()
  if (Test-Path $EnvFile) {
    $lines = Get-Content $EnvFile | Where-Object { $_ -notmatch "^$([regex]::Escape($Key))=" }
  }
  $lines += "$Key=$Value"
  Set-Content -Path $EnvFile -Value $lines -Encoding UTF8
}

function New-FernetKey {
  # Fernet key = url-safe base64 of 32 random bytes.
  $bytes = New-Object 'System.Byte[]' 32
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  return [Convert]::ToBase64String($bytes).Replace('+','-').Replace('/','_')
}

Write-Host '----------------------------------------------'
Write-Host ' JobPilot setup'
Write-Host '----------------------------------------------'

if (Test-Path $EnvFile) {
  $ow = Ask 'An .env already exists. Overwrite it? (y/N)' 'N'
  if ($ow -notmatch '^(y|yes)$') { Write-Host 'Keeping existing .env. Nothing changed.'; exit 0 }
}
Copy-Item $Example $EnvFile -Force

# Clear the example's placeholder credentials/routing so the result only
# contains what you actually choose below (placeholders otherwise read as
# real, non-empty values).
foreach ($k in @(
  'ADZUNA_APP_ID','ADZUNA_APP_KEY','OPENAI_API_KEY','ANTHROPIC_API_KEY',
  'SERPAPI_KEY','LLM_API_KEY','LLM_BASE_URL','LLM_MODEL','EMBEDDING_API_KEY','EMBEDDING_BASE_URL',
  'BROWSER_LLM_API_KEY','BROWSER_LLM_BASE_URL','BROWSER_LLM_MODEL')) {
  Set-Env $k ''
}

Write-Host ''
Write-Host 'Which AI provider should JobPilot use?'
Write-Host '  1) Local / self-hosted  (OpenAI-compatible: Ollama, llama.cpp, LM Studio, vLLM)'
Write-Host '  2) OpenAI-compatible    (cloud; hosted OpenAI or any OpenAI-compatible endpoint)'
Write-Host '  3) Anthropic            (cloud; generation only - see notes)'
$choice = Ask 'Choice' '1'

switch ($choice) {
  '1' {
    Write-Host ''
    Write-Host 'Tip: from inside Docker, a model on THIS machine is reachable as'
    Write-Host '     http://host.docker.internal:<port>/v1  (not http://localhost).'
    $base  = Ask 'Model base URL' 'http://host.docker.internal:11434/v1'
    $model = Ask 'Model name (as the server reports it)' ''
    Set-Env 'LLM_PROVIDER' 'openai'
    Set-Env 'LLM_BASE_URL' $base
    if ($model) { Set-Env 'LLM_MODEL' $model }
    Set-Env 'BROWSER_LLM_PROVIDER' 'openai'
    Set-Env 'BROWSER_LLM_BASE_URL' $base
    if ($model) { Set-Env 'BROWSER_LLM_MODEL' $model }
    Write-Host ''
    Write-Host 'Embeddings power job/CV fit-scoring. Most local servers do NOT serve'
    Write-Host 'embeddings, so you can point them at a hosted OpenAI-compatible key or skip scoring.'
    $ek = Ask 'API key for embeddings (blank = use the local server)' ''
    if ($ek) {
      Set-Env 'EMBEDDING_PROVIDER' 'openai'
      Set-Env 'EMBEDDING_API_KEY' $ek
    } else {
      # No cloud key: point embeddings at the same local server. Config stays
      # valid and the app boots; if the server lacks /v1/embeddings, fit-scoring
      # degrades at runtime instead of blocking startup.
      Set-Env 'EMBEDDING_PROVIDER' 'openai'
      Set-Env 'EMBEDDING_BASE_URL' $base
      Write-Host "  (embeddings will use $base - fit-scoring needs an embeddings-capable server)"
    }
  }
  '2' {
    $ok = Ask 'API key (sk-... for hosted OpenAI)' ''
    $burl = Ask 'Base URL (blank = hosted OpenAI; set for another OpenAI-compatible endpoint)' ''
    Set-Env 'LLM_PROVIDER' 'openai'
    Set-Env 'EMBEDDING_PROVIDER' 'openai'
    Set-Env 'BROWSER_LLM_PROVIDER' 'openai'
    Set-Env 'OPENAI_API_KEY' $ok
    if ($burl) {
      Set-Env 'LLM_BASE_URL' $burl
      Set-Env 'EMBEDDING_BASE_URL' $burl
      Set-Env 'BROWSER_LLM_BASE_URL' $burl
    }
  }
  '3' {
    $ak = Ask 'Anthropic API key (sk-ant-...)' ''
    Set-Env 'LLM_PROVIDER' 'anthropic'
    Set-Env 'ANTHROPIC_API_KEY' $ak
    Write-Host ''
    Write-Host 'Note: Anthropic has no embeddings API and browser-use ships no Anthropic'
    Write-Host 'client, so embeddings + the browser agent fall back to an OpenAI-compatible endpoint.'
    $ek = Ask 'OpenAI-compatible API key for embeddings + browser agent' ''
    Set-Env 'EMBEDDING_PROVIDER' 'openai'
    Set-Env 'BROWSER_LLM_PROVIDER' 'openai'
    if ($ek) { Set-Env 'OPENAI_API_KEY' $ek }
  }
  Default { Write-Host 'Unknown choice - re-run and pick 1-3.'; exit 1 }
}

Write-Host ''
Write-Host 'Adzuna powers the job-search source (developer.adzuna.com, free). Optional.'
$aid = Ask 'ADZUNA_APP_ID (blank to skip)' ''
if ($aid) {
  $akey = Ask 'ADZUNA_APP_KEY' ''
  Set-Env 'ADZUNA_APP_ID' $aid
  Set-Env 'ADZUNA_APP_KEY' $akey
}

# Persistent credential-encryption key (critical for Docker: env_file is not
# writable from inside the container, so a generated-at-runtime key would be
# lost on every restart, breaking stored-credential decryption).
Set-Env 'CREDENTIAL_KEY' (New-FernetKey)

Write-Host ''
Write-Host "OK - wrote $EnvFile"
Write-Host ''
Write-Host 'Next:'
Write-Host '  Docker:    docker compose up -d --build   ->  http://localhost:8000'
Write-Host '  Local dev: uv run python start.py'
