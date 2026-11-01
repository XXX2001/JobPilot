# backend package
import os

# W02 / F21 — local-privacy mode. browser-use defaults to PostHog telemetry
# (agent task text, URLs, action history) and cloud sync. JobPilot embeds
# applicant details in agent tasks, so both are forced off here: every entry
# point (uvicorn backend.main:app, start.py, pytest, the container) imports
# this package before browser_use, and browser_use reads these at use time.
os.environ["ANONYMIZED_TELEMETRY"] = "false"
os.environ["BROWSER_USE_CLOUD_SYNC"] = "false"
