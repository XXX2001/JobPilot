# JobPilot — LLM / Token-Efficiency Audit (2026-05-22)

Scope: `backend/llm/`, `backend/applier/`, `backend/matching/`, `backend/scraping/` and
their callers in `backend/scheduler/` / `backend/latex/` / `backend/api/`.
Subject: Google Gemini usage via `backend.llm.gemini_client.GeminiClient`.

Sibling report (separate concern, do not duplicate):
`docs/reports/2026-05-22-standards/` (naming, error-handling, type-safety, structure).
This report **only** covers token / API spend and prompt quality.

---

## TL;DR

1. **Zero use of Gemini context caching.** Every call rebuilds the full prompt — system instructions, JSON schema, full CV LaTeX body, full job context — and ships them again. The CV body alone (≤ 50 KB, ~12 k tokens) is sent twice per job (analyze + modify) and re-sent on every retry. Implicit caching only activates if the *prefix* is identical across calls — which today it is not, because the variable job description is interpolated **at the start of the user message**, defeating any free implicit cache. Estimated waste: **40-70 % of CV-tailoring input tokens**.
2. **Model tier is monolithic.** `settings.GOOGLE_MODEL = "gemini-3-flash-preview"` is used for every task — scraping extraction, form-field mapping, job analysis, CV editing, letter editing, description enrichment, and (via `ChatGoogle`) the browser-use Tier-2 agent loops. There is no Flash-Lite path for the cheap-extraction tasks (Scrapling JSON pull, form mapping) and no Pro path for the high-leverage CV editing. The `GEMINI_FALLBACK_MODEL = "gemini-2.0-flash"` is a *failure fallback*, not a *task router*.
3. **Embeddings have no persistence and no production wiring.** `Embedder` is never instantiated in `backend/main.py` lifespan — only kept as an `Optional[Embedder] = None` parameter on `MorningBatchRunner`. The whole FitEngine / gap-driven path is dead in production. Worse, even if wired, `SkillEntry.embedding` lives only in memory (no DB column), so every batch re-embeds the user's CV from scratch — ~30-80 embedding calls per run.
4. **Job-context cache is per-process and per-`CVPipeline` instance**, max 100 entries, TTL 1 h, never persisted. A server restart or the regenerate-document endpoint each cause a fresh `JobAnalyzer.analyze()` call for jobs that were analyzed minutes ago. SHA-256 hash on `CVProfile` is computed but never used to look anything up.
5. **No batching for the embarrassingly batchable.** `MorningBatchRunner._gen_one` issues *up to N* independent JobAnalyzer calls (one per top match) when a single call with a job-array could analyze 5-10 jobs at once. Same for ScraplingFetcher across keywords.
6. **Free-text response paths still exist where JSON mode would work.** `ScraplingFetcher._extract_jobs` (`scrapling_fetcher.py:393`), `PlaywrightFormFiller` (`form_filler.py:177`), and `documents.regenerate`-style enrichment (`queue.py:316`) all use `generate_text()` plus regex/JSON salvage, when `generate_json()` with `response_schema` would eliminate the entire "no JSON found / parse error" failure mode and the silent default-on-error returns that mask real failures.
7. **Prompt-injection wrappers are good, but every prompt mixes system rules + data in one user-message string.** Gemini supports `system_instruction` separately from `contents`. Splitting is both safer (clearer trust boundary) and cheaper (the system block is exactly what the implicit cache rewards for being invariant). Today nothing in the codebase passes `system_instruction`.

---

## Findings table

| ID | Title | Severity | File:line | Estimated saving |
|---|---|---|---|---|
| LL-01 | No Gemini context caching (implicit or explicit) | **High** | `backend/llm/gemini_client.py:146-217` | 40-70 % CV-tailoring input tokens |
| LL-02 | One-model-fits-all — no Flash-Lite for cheap tasks | **High** | `backend/config.py:56`, all call sites | 50-80 % per cheap call; 30 % overall |
| LL-03 | Embedder not wired in `main.py` lifespan (dead code in prod) | **High** | `backend/main.py:113-150` (absence) | N/A — feature gap, not waste |
| LL-04 | Embeddings never persisted to DB — recomputed every batch | **High** | `backend/matching/embedder.py`, `backend/models/*` | 100 % of redundant embed calls (~30-80/batch) |
| LL-05 | Free-text + regex salvage where JSON mode would work | Med | `backend/scraping/scrapling_fetcher.py:393-398`; `backend/applier/form_filler.py:177,340,503-530`; `backend/api/queue.py:316` | Eliminates retry/failure loss + ~5 % tokens |
| LL-06 | Job-context cache is in-process only + opaque to `/regenerate` | Med | `backend/latex/pipeline.py:109,191-204` | 1 redundant `JobAnalyzer` call per restart per job (~3 k tokens each) |
| LL-07 | CV body sent twice per tailoring run (analyze + modify) | Med | `backend/llm/job_analyzer.py:53-65` + `backend/llm/cv_modifier.py:92-107` | ~30-50 % of CV-tailoring tokens if cached / referenced |
| LL-08 | No `system_instruction` split — everything is one user message | Med | `backend/llm/gemini_client.py:146-217`, all prompts | Enables implicit caching; ~20-30 % cost cut at scale |
| LL-09 | Self-healing JSON retry retries the full prompt without JSON mode | Med | `backend/llm/gemini_client.py:248-263` | Avoids a full 2nd round-trip on every parse error |
| LL-10 | No batching — JobAnalyzer / Scrapling extraction run one at a time | Med | `backend/scheduler/morning_batch.py:397-444`; `backend/scraping/orchestrator.py:240-298` | 60-80 % reduction in request count |
| LL-11 | `temperature` and `max_output_tokens` are never set | Low | `backend/llm/gemini_client.py:166-191` | Avoids long-tail output runaway; ~10 % p99 |
| LL-12 | Description enrichment uses 20 KB raw `cleaned[:20000]` per job | Med | `backend/api/queue.py:308-316` | 50-80 % per call if summarized |
| LL-13 | Two separate 15-RPM deques (gen vs embed) limit throughput unnecessarily | Low | `backend/llm/gemini_client.py:121-124, 265-275` | Quota = throughput; better routing helps |
| LL-14 | Letter prompt re-sends entire (job-title + 500-char excerpt) every call, no cache | Low | `backend/llm/cv_editor.py:99-106` | Small; ~200-400 tokens/call but easy fix |
| LL-15 | `CVProfile.raw_text_hash` computed but never consulted | Low | `backend/matching/cv_parser.py:160-161` | Currently zero benefit — dead code or unfinished work |
| LL-16 | Two parallel `ChatGoogle()` instances spun up per browser-use apply (fill + submit) | Low | `backend/applier/auto_apply.py:349-352, 464-467` | Re-init only; agent loops dominate cost |

---

## Per-finding details

### LL-01 — No Gemini context caching at all (High)

**Problem.** `GeminiClient` calls `client.models.generate_content(model=..., contents=prompt, config=...)` on every request. It never (a) sets a `system_instruction` (which Gemini implicitly caches when stable), (b) creates an explicit cache via `client.caches.create(...)`, nor (c) orders the prompt so that the invariant portion is a strict prefix (which is the only way implicit caching ever triggers).

In `JobAnalyzer` (`backend/llm/job_analyzer.py:59-64`) the prompt is built by `.format(...)` directly interpolating job_title / company / job_description **at line ~16** of the template, *before* the CV. In `CVModifier.modify` (`backend/llm/cv_modifier.py:102-106`) the `context_md` (variable) precedes `cv_tex` (mostly invariant for one user). In both cases there is **no stable prefix** to cache.

**Evidence.**
- `backend/llm/gemini_client.py:146-217` — no `system_instruction`, no `cached_content`.
- `backend/llm/prompts.py:45-86` (JOB_ANALYZER_PROMPT) — job posting is interpolated *before* the CV block.
- `backend/llm/prompts.py:88-161` (CV_MODIFIER_SKILL) — variable `{job_context_md}` precedes the (mostly invariant) `{cv_tex}` block. CV body is ~10-30 k chars on real CVs; it is shipped wholesale every call.
- `backend/llm/cv_modifier.py:92-106` — `body = _strip_preamble(cv_tex); body = body[:50_000]` then `prompt = CV_MODIFIER_SKILL.format(...)`. No cache lookup.

**Proposed change.**
1. Split prompts into two parts:
   - System instructions + the user's CV body → pass as `system_instruction=` (and as cached content for repeat users).
   - Per-job context only → pass as user `contents=`.
2. For users running >1 application/day, create an explicit cache once per CV version:
   ```python
   cache = client.caches.create(
       model="models/gemini-2.5-flash",
       config=CreateCachedContentConfig(
           system_instruction=CV_MODIFIER_SYSTEM,
           contents=[Content(role="user", parts=[Part(text=cv_body)])],
           ttl="3600s",
       ),
   )
   # subsequent calls
   client.models.generate_content(
       model="models/gemini-2.5-flash",
       contents=[job_context_only],
       config=GenerateContentConfig(cached_content=cache.name),
   )
   ```
   Cached tokens are billed at ~25 % of standard input.
3. At minimum, **re-order** the prompt strings so the invariant block (rules + CV) is the prefix, then the variable job block is the suffix. That alone unlocks implicit caching when the user processes ≥ 2 jobs back-to-back.

**Estimated saving.** With a 30 KB CV (~7-8 k tokens) and 5-job batches, current input usage per batch ≈ 35-40 k tokens; with reordering + implicit caching, ≈ 12-15 k. With explicit cache: ≈ 8 k. **40-70 %** reduction on CV-tailoring input cost.

---

### LL-02 — Single model for every task (High)

**Problem.** `settings.GOOGLE_MODEL = "gemini-3-flash-preview"` (`backend/config.py:56`) is wired through `GeminiClient` and used identically for: ScraplingFetcher job extraction, PlaywrightFormFiller form mapping, browser-use Tier-2 agent loops, JobAnalyzer, CVModifier, CVEditor (letter), description enrichment. Same temperature, same model, same `max_output_tokens` (none — all implicit).

Tasks split roughly into three buckets:

| Bucket | Tasks | Right model |
|---|---|---|
| Cheap structured extraction | Scrapling JSON pull, form-field mapping, description enrichment | Flash-Lite (or Flash with strict `max_output_tokens` cap) |
| Medium reasoning | JobAnalyzer, LetterEdit | Flash |
| High-stakes editing | CVModifier (surgical replacements with confidence gating) | Pro (or Flash with `thinking_config` enabled) |
| Agent loops | browser-use Tier-2 | Flash (~5-30 LLM calls/run) |

Today the codebase commits 100 % to Flash. ScraplingFetcher specifically calls a single Gemini per page (`backend/scraping/scrapling_fetcher.py:393-398`) — perfect Flash-Lite work, but routed to Flash.

**Evidence.**
- `backend/config.py:56-58` — single `GOOGLE_MODEL` field, single `GOOGLE_MODEL_FALLBACKS` (failure fallback only).
- `backend/llm/gemini_client.py:111-120` — `_candidates` list is built only for *failover*, not task routing.
- `backend/applier/auto_apply.py:94, 349-352, 464-467` — `ChatGoogle(model=self._model, …)` reuses the same primary.
- `backend/scraping/adaptive_scraper.py:64` — `ChatGoogle(model=model, …)` same.

**Proposed change.**
1. Replace `GOOGLE_MODEL: str` with a per-task router:
   ```python
   class ModelRouter:
       extraction: str = "gemini-2.5-flash-lite"
       analysis:   str = "gemini-2.5-flash"
       editing:    str = "gemini-2.5-pro"  # or flash with thinking_config
       agent:      str = "gemini-2.5-flash"
   ```
2. `GeminiClient.generate_text(..., task: TaskKind)` looks up the model per call.
3. ScraplingFetcher, PlaywrightFormFiller, and the queue-enrich endpoint switch to `extraction` (Flash-Lite). CVModifier and CVEditor stay on `editing`. browser-use Agents stay on `agent`.

**Estimated saving.** Flash-Lite is roughly 1/3 the input cost of Flash and 1/5 the output cost. ScraplingFetcher alone fires once per site*keyword*kw in `MorningBatchRunner` — easily 15-30 calls/batch. **30 % overall cost cut** is a conservative estimate.

---

### LL-03 — `Embedder` is dead code in production (High)

**Problem.** `MorningBatchRunner.__init__` takes `embedder: Embedder | None = None` (`backend/scheduler/morning_batch.py:184-205`). `backend/main.py:144-149` instantiates `MorningBatchRunner(scraper=…, matcher=…, cv_pipeline=…, db_factory=…)` — **no `embedder=`, no `fit_engine=`**. So every prod run hits this branch:

```python
# scheduler/morning_batch.py:328
if cv_profile and self._embedder:   # ← always False
    for mid in new_match_ids:
        ...
```

…meaning the **FitEngine path never runs**. Every match falls through to the `assessment is None` branch in `_gen_one` (`morning_batch.py:433-439`), which calls `generate_tailored_cv` *without* `fit_assessment`, which in turn runs the **fallback** JobAnalyzer + CVModifier path. The whole gap-driven optimization (which exists explicitly to skip Gemini for jobs where the CV already covers everything: `assessment.should_modify=False` → `generate_base_cv` with **zero LLM calls** at `pipeline.py:182-187`) is bypassed.

**Evidence.**
- `backend/main.py:144-149` — no `embedder=` arg.
- `backend/scheduler/morning_batch.py:328` — branch guard never True.
- `backend/latex/pipeline.py:182-187` — base-CV-only branch saves *two* Gemini calls per job whenever `assessment.should_modify=False`. In a 10-job batch where 5 jobs match well, that's 10 calls saved.

**Proposed change.** In `main.py` lifespan:
```python
from backend.matching.embedder import Embedder
from backend.matching.fit_engine import FitEngine
embedder = Embedder(gemini_client=gemini)
fit_engine = FitEngine()
batch_runner = MorningBatchRunner(
    scraper=orchestrator, matcher=matcher, cv_pipeline=cv_pipeline,
    db_factory=AsyncSessionLocal,
    embedder=embedder, fit_engine=fit_engine,
)
```

**Estimated saving.** Hard to quantify without prod data, but on a 10-job batch with a well-matched CV, FitEngine typically marks 30-60 % of jobs as `should_modify=False`. Skipping those = ~5-10 × (JobAnalyzer + CVModifier) Gemini calls avoided ≈ **30-50 % of per-batch CV-tailoring tokens**. This is also a correctness fix: the feature was designed and shipped but is unreachable.

---

### LL-04 — Embeddings have no persistence (High)

**Problem.** `SkillEntry.embedding: list[float]` and `JobSkill.embedding: list[float]` live only on the dataclass instances. `CVProfile` and `JobProfile` are not ORM models; nothing in `backend/models/*` has an `embedding` column. `Embedder.embed_*_profile` will skip already-embedded items *only within the same process call* — the moment the function returns and the profile is dropped, the embeddings are gone.

Concretely: `MorningBatchRunner._run_batch_inner` calls `self._embedder.embed_cv_profile(cv_profile)` once per batch run (`morning_batch.py:317`). If `Embedder` is wired (per LL-03), every batch invocation re-embeds every CV skill — typically 20-50 skills — and every job profile, for every batch, even though the CV hash is right there in `CVProfile.raw_text_hash`. The hash is computed at `cv_parser.py:160` but is never read.

**Evidence.**
- `grep -rn "embedding" backend/models/` → no hits. There is no `cv_skill_embeddings` table.
- `backend/matching/cv_parser.py:160-161` — `raw_text_hash` computed.
- Nothing in the repo consults `raw_text_hash`.

**Proposed change.**
1. Add a small `skill_embeddings` table keyed by `(skill_text, model_version)` storing the vector blob.
2. `Embedder.embed_*` first batches a DB lookup for `(text, model_version)` rows; only un-cached texts go to Gemini.
3. Use `CVProfile.raw_text_hash` to short-circuit *parse + embed* entirely: if the same hash was seen on the same model version, load the prior `CVProfile` from cache.

**Estimated saving.** First run: same cost. Subsequent runs (same CV): **100 % of CV-embedding API cost eliminated** (~20-50 calls). Job-skill embeddings reused across batches because common skills (Python, AWS, SQL, …) repeat across postings — typical hit-rate after 1 week ≈ 60-80 %.

---

### LL-05 — Free-text + regex salvage instead of JSON mode (Med)

**Problem.** Three call sites ask Gemini for JSON in free text, then regex-extract:

1. `backend/scraping/scrapling_fetcher.py:393-403` — `_extract_jobs` calls `generate_text(prompt)`; the caller does `extract_json_from_text(raw_text)` + `parse_jobs_from_json(...)`. No `response_mime_type`, no `response_schema`.
2. `backend/applier/form_filler.py:177` (`generate_text(prompt)`) and `:503-530` (`_parse_gemini_response` strips markdown fences, regexes the first `{ … }`, falls back to a hard-coded "submit_selector": "button[type=submit]" default on any parse error — *silently returning a default mapping that won't fill anything*).
3. `backend/api/queue.py:316` — `generate_text(prompt)` for description enrichment, returns the body verbatim (no schema needed here, OK).

The form-filler default-on-error path (`form_filler.py:510, 519, 524-530`) is a **silent failure mode**: if Gemini's response doesn't parse, the strategy proceeds with `{"fields": [], "file_inputs": [], "submit_selector": "button[type=submit]"}`, fills no fields, and triggers a user "review" message anyway. The user has to spot the empty form themselves. That's the worst of both worlds: tokens spent and no automation.

**Evidence.**
- `backend/scraping/scrapling_fetcher.py:393-398` — single line of LLM logic, no schema.
- `backend/applier/form_filler.py:503-530` — full silent-default branch.
- `backend/llm/gemini_client.py:219-263` — `generate_json` exists and supports a Pydantic schema. **It is used by `JobAnalyzer`, `CVModifier`, `CVEditor` but not by the scraping / form-filler paths.** Inconsistent.

**Proposed change.**
1. Define `RawJobList(BaseModel)` and `FormFillPlan(BaseModel)` and switch both call sites to `generate_json(prompt, schema)`.
2. Have `form_filler` raise `GeminiJSONError` on parse failure (let `AutoApplyStrategy` catch and fall through to Tier 2). Don't silently fill nothing.

**Estimated saving.** ~5 % tokens on the response side (no `"\`\`\`json\n…\n\`\`\`"` wrapping), but the bigger win is eliminating the silent-failure mode and the no-op retry-without-JSON-mode in `generate_json` (LL-09).

---

### LL-06 — Job-context cache is in-process only, opaque to `/regenerate` (Med)

**Problem.** `CVPipeline._context_cache: dict[int, tuple[float, object]]` (`backend/latex/pipeline.py:109`) caches `JobContext` objects for 1 h, max 100, keyed by `job.id`. Two issues:

1. The cache lives on the `CVPipeline` *instance*. A server restart wipes it. The regenerate endpoint at `backend/api/documents.py:223-267` doesn't actually call the pipeline (it just marks docs stale and returns "queued" — this is also flagged in the standards backlog as RG-01); whenever a real regeneration is wired up, it will be a cold cache call.
2. The cache key is `job.id` (int) but the *content* of the cached JobContext depends on the CV (`cv_content=cv_tex`) too. If the user updates their CV, the cache will return stale analysis tied to the old CV — bad correctness *and* hides waste.

**Evidence.**
- `backend/latex/pipeline.py:109` — definition.
- `backend/latex/pipeline.py:191-204` — `_context_cache` read/write.
- `backend/llm/job_analyzer.py:42-65` — `JobContext` is a function of `(job, cv_content)`, not just `job`.

**Proposed change.** Persist `JobContext` to a small `job_contexts` table keyed by `(job_id, cv_hash, model_version)`. The CV hash already exists (`raw_text_hash`, LL-15). Reads survive restarts; cache invalidates correctly on CV change.

**Estimated saving.** 1 redundant ~3 k-token `JobAnalyzer` call per (restart, job) and per (CV-edit, job). With a single dev cycle a few restarts a day, this is a few thousand tokens / day saved, but the correctness win is the real point.

---

### LL-07 — CV body sent twice per tailoring run (Med)

**Problem.** A single tailored-CV generation calls:
1. `JobAnalyzer.analyze(job, cv_content=cv_tex)` → ships `cv_text` truncated to 3 000 chars (`job_analyzer.py:58`).
2. `CVModifier.modify(job, cv_tex, context, …)` → ships full CV body up to 50 KB (`cv_modifier.py:93-95`).

Both calls embed the CV inline. The 3 KB version isn't useless — it's a contextual hint for JobAnalyzer to compute `candidate_matches` vs `candidate_gaps` — but it's *also* re-derivable from a structured `CVProfile` (parsed skill list with weights), which is already produced by `CVParser.build_profile` upstream. The matching engine already has the structured representation; passing it as a tight skill list to JobAnalyzer would cut JobAnalyzer's CV portion from 3 000 chars to a few hundred and keep CV body shipping to one (CVModifier) call.

**Evidence.**
- `backend/llm/job_analyzer.py:53-65` — sends sanitized CV text.
- `backend/llm/cv_modifier.py:92-107` — sends CV body again.
- `backend/matching/cv_parser.py:112-151` — already produces a structured skill list.

**Proposed change.** Pass `CVProfile.skills` (compact: `["Python (recent)", "Docker (skills)", ...]` ~ 30-80 tokens) to JobAnalyzer instead of raw CV text. Reserve sending the full CV body only to CVModifier (and only once, ideally via the cache from LL-01).

**Estimated saving.** Eliminates 3 000 chars × ~250 tokens/k = ~750 tokens × number of analyze calls. With caching from LL-01 the saving compounds.

---

### LL-08 — No `system_instruction` split (Med)

**Problem.** Every prompt is shipped as one giant user-role string. Gemini's API supports `GenerateContentConfig(system_instruction=...)`. System instructions are (a) cached more aggressively by Gemini's implicit cache, (b) treated by the model as higher-trust than user content (improves prompt-injection resistance — already a design concern, see `<untrusted_data>` wrappers in `prompts.py`), and (c) a natural place to put the rules, JSON schema, and "you are a surgical CV editor" framing that never change.

**Evidence.**
- `backend/llm/gemini_client.py:170-174` — `GenerateContentConfig` is built but only sets `response_mime_type` and `response_schema`.
- `backend/llm/prompts.py:88-161` (CV_MODIFIER_SKILL) — ~80 lines of invariant rules mixed in with the variable job context and CV body.

**Proposed change.** Refactor `GeminiClient.generate_text` / `generate_json` to accept a `system_instruction: str | None = None`. Each prompt template splits cleanly into a constant system block (rules, schema, output format) and a small user block (just the variable data). For Pydantic schemas there's no change — `response_schema` is independent.

**Estimated saving.** Once the prefix is invariant, Gemini's implicit cache kicks in around the 1-2 k-token mark for free (no API call to create a cache). For high-frequency users this cuts the cached portion to ~25 % of standard input pricing.

---

### LL-09 — Self-healing JSON retry re-sends the whole prompt (Med)

**Problem.** `generate_json` (`backend/llm/gemini_client.py:248-263`) on parse failure calls `self.generate_text(prompt)` *without* JSON mode — a full second round-trip with the same prompt. This is a double-charge for a transient model glitch. In native JSON mode (`response_mime_type="application/json"` with a `response_schema`) Gemini almost never returns invalid JSON; when it does, the right move is to (a) log and (b) raise so the caller can decide, **not** silently re-spend tokens on a non-JSON retry that will then need its own regex salvage.

**Evidence.**
- `backend/llm/gemini_client.py:248-263` — `text2 = await self.generate_text(prompt)` on failure.
- The comment "JSON mode should prevent this" is accurate — the retry exists as a hedge against schemas the model rejects. But the response_schema path itself already has a try/except.

**Proposed change.** Drop the second-attempt retry. Log + raise `GeminiJSONError`. If a schema is incompatible, that's a code bug to fix, not a runtime hedge.

**Estimated saving.** Eliminates a worst-case 2× token cost per failed parse. Rare in practice but pure waste when it triggers.

---

### LL-10 — No batching for analyze / extract (Med)

**Problem.** Each top-N job in `MorningBatchRunner._gen_one` issues an *independent* `JobAnalyzer.analyze()` call (and a separate `CVModifier.modify()` call). With `CONCURRENCY_GEMINI = 3` (`backend/defaults.py:37`) and 10 top jobs, that's at minimum 10 analyze + 10 modify calls. Gemini supports passing multiple jobs in a single prompt with a structured response_schema (`list[JobContext]`) — for analysis the prompt cost amortizes across jobs (one rules block, N job blocks).

Similarly, `ScraplingFetcher.scrape_job_listings` is called once per (site, keyword) pair (`backend/scraping/orchestrator.py:240-298`). Two keywords × five sites = 10 LLM calls per batch; the cleaned-page extraction prompts are independent and could be parallelized properly (today the orchestrator serializes browser_sources for human-like delay, but that's a scraping concern, not an LLM concern).

For analyze specifically there's also the Gemini **Batch API** (asynchronous, lower priced) for non-interactive workloads — the morning batch is a perfect fit.

**Evidence.**
- `backend/scheduler/morning_batch.py:442-444` — `asyncio.gather` of `_gen_one` tasks. Each task is its own LLM call.
- `backend/llm/job_analyzer.py:41-65` — `analyze` takes one job.
- `backend/scraping/scrapling_fetcher.py:393-398` — `_extract_jobs` per page.

**Proposed change.**
1. Add `JobAnalyzer.analyze_batch(jobs: list[JobDetails], cv_profile: CVProfile) -> list[JobContext]` that ships 5-10 jobs per prompt with a `list[JobContext]` response_schema.
2. Wire the morning batch to call `analyze_batch` once and then run `CVModifier.modify` (which is per-job by nature — different replacements per job) under the semaphore.
3. Consider the Gemini Batch API for the analyze step since it's not user-blocking.

**Estimated saving.** 60-80 % fewer requests for the analyze step; aggregated input tokens drop ~30-40 % due to shared rules prefix.

---

### LL-11 — `temperature` and `max_output_tokens` are never set (Low)

**Problem.** `GenerateContentConfig` is constructed only when `response_mime_type` is set, and only with the schema fields (`backend/llm/gemini_client.py:170-174`). Temperature defaults to Gemini's server-side default (~1.0 for generation); `max_output_tokens` is unbounded. For surgical-editing tasks where determinism matters (CVModifier should not "improvise" extra replacements), `temperature=0.2` is appropriate. For extraction (Scrapling, form-filler) `temperature=0.0` is the right answer. For free-text creative letter editing, `0.5-0.7` is reasonable.

Unbounded `max_output_tokens` lets the model occasionally produce a 4 k-token explanation that nobody asked for; for `LetterEdit` the response is one paragraph (~150 tokens), for `CVModifierOutput` at most 3 replacements (~500 tokens), for the form-fill JSON typically <1 KB.

**Proposed change.** Add `temperature` and `max_output_tokens` to each call site, passed through `GeminiClient`. Reasonable caps per task: extraction 2 048, analysis 2 048, CV-edit 1 024, letter-edit 512.

**Estimated saving.** Small in expectation, but caps the p99 cost.

---

### LL-12 — Description enrichment uses 20 KB raw page content (Med)

**Problem.** `backend/api/queue.py:308-316`:
```python
cleaned = fetcher._clean_html(html)
prompt = (
    "Extract the FULL job description from the page content below…"
    f"Page content:\n{cleaned[:20000]}"
)
description = await gemini_client.generate_text(prompt)
```

20 000 characters ≈ 5 000 tokens. For a job-description-extraction task that's already had `_clean_html` strip script/style/nav/footer, the relevant content is typically <5 KB. The 20 KB cap is `MAX_SCRAPLING_CONTENT_CHARS // 2.5` with no measurement to back it up.

Also note this endpoint calls a *private* method (`fetcher._clean_html`) which is its own design smell flagged in NM-05 of the standards backlog.

**Evidence.** `backend/api/queue.py:308-316`.

**Proposed change.** (a) Cap at 8 KB. (b) Use Flash-Lite (LL-02). (c) For long pages, do a quick "is this a job description?" pre-filter and a "main content" extraction via lxml's `text_content()` on the most-content `<div>` rather than shipping the whole cleaned page.

**Estimated saving.** 50-80 % per enrichment call.

---

### LL-13 — Two separate 15-RPM deques for gen vs embed (Low)

**Problem.** `GeminiClient` maintains `_call_times` (gen) and `_embed_call_times` (embed) deques (`backend/llm/gemini_client.py:121-124, 265-275`), each capped at 15 RPM. Gemini's free-tier limits are per-*model* (and the embedding endpoint has its own much higher RPM limit). The two-deque split is correct in principle — but `RPM_LIMIT = 15` is hardcoded for the (assumed) generation tier, and embeddings should not be throttled at the same rate.

**Evidence.** `backend/llm/gemini_client.py:109` — `RPM_LIMIT = 15` shared by both deques.

**Proposed change.** Separate `GEN_RPM` and `EMBED_RPM` constants. `text-embedding-004` allows ~1 500 RPM on the free tier; throttling it at 15 RPM wastes embedding throughput when the morning batch is processing dozens of new job skills.

**Estimated saving.** Wall-clock time on batch, not tokens — but it's a free fix.

---

### LL-14 — Letter prompt is short but repeats the framing every call (Low)

**Problem.** `MOTIVATION_LETTER_PROMPT` (`backend/llm/prompts.py:19-43`) is ~600 tokens — small enough that caching wouldn't help much. But the customizable paragraph and rules are identical across every job for one user; only `{job_title}`, `{company}`, `{job_description_excerpt}`, and `{letter_content}` vary. Same opportunity for `system_instruction` split as LL-08.

**Proposed change.** Lift the rules block to `system_instruction`. Pass only the four variables in `contents`.

**Estimated saving.** Small in absolute terms; trivial change.

---

### LL-15 — `CVProfile.raw_text_hash` is computed but never read (Low)

**Problem.** `cv_parser.py:160-161` computes a SHA-256 of the LaTeX source. Nothing in the codebase consults it. It's dead infrastructure — useful when LL-04 / LL-06 land.

**Proposed change.** Either delete it (until used) or wire it through Embedder + JobContext caches.

---

### LL-16 — Two `ChatGoogle` instances per browser-use apply (Low)

**Problem.** `AutoApplyStrategy._browser_use_apply` creates `llm = ChatGoogle(model=…, api_key=…)` for the fill phase (`backend/applier/auto_apply.py:349-352`) and a second `llm2 = ChatGoogle(model=…, api_key=…)` for the submit phase (`:464-467`). Init is cheap, but each `Agent.run()` is itself a multi-LLM-call loop; reusing the agent's accumulated conversation state across phases would let the model retain context about which fields are filled already.

**Proposed change.** Reuse the same `Agent` across fill and submit phases, or at least pre-warm the second LLM with a small "summary" message rather than starting a completely fresh agent.

**Estimated saving.** Hard to quantify without measuring the agent step counts. Order of magnitude: 2-5 fewer steps in the submit agent loop.

---

## Quick-win checklist (top 5 by ROI)

1. **Wire `Embedder` and `FitEngine` in `main.py` lifespan** (LL-03). One-file change in `backend/main.py:144-149`. Activates the whole "skip CV-tailoring when fit is already great" path which is already implemented but unreachable. Expected: **30-50 % fewer JobAnalyzer + CVModifier calls per batch**. *Effort: trivial.*
2. **Reorder prompts: invariant prefix first, variable suffix last** (LL-01 partial). Edit `JOB_ANALYZER_PROMPT` and `CV_MODIFIER_SKILL` in `prompts.py` so the CV / rules block precedes the per-job context block. Unlocks Gemini's implicit cache for free. Expected: **20-40 % input-token reduction** for users running ≥ 2 jobs/session. *Effort: small.*
3. **Route Scrapling and form-filler calls to Flash-Lite** (LL-02 partial). Add a `model_extraction: str = "gemini-2.5-flash-lite"` setting and pass it explicitly to `ScraplingFetcher` and `PlaywrightFormFiller`. Expected: **50-70 % cost cut on the highest-volume call paths**. *Effort: small.*
4. **Switch ScraplingFetcher and PlaywrightFormFiller to `generate_json` with `response_schema`** (LL-05). Eliminates silent "no JSON found → default" failure mode and the half-token wrapping waste. *Effort: small.*
5. **Persist embeddings in a `skill_embeddings` table keyed by `(text, model)`** (LL-04). After the first run, ~60-80 % of job-skill embedding calls disappear. *Effort: medium — requires a small migration.*

(6. **Wire `system_instruction` through `GeminiClient`** (LL-08) — combines well with #2 to unlock the most aggressive implicit caching. Medium effort but high leverage.)

---

## Already good

- **Native JSON mode is used by `generate_json`** (`gemini_client.py:230-238`) — passes both `response_mime_type` and `response_schema` derived from a Pydantic model. JobAnalyzer / CVModifier / CVEditor all benefit. Just not extended to Scrapling / form-filler (LL-05).
- **Pydantic schemas for LLM I/O are tight** (`backend/llm/validators.py`, `backend/llm/job_context.py`) — `CVReplacement.confidence` validator clamps to [0, 1]; `CVModifierOutput.top_three()` enforces the 3-replacement cap *outside* the LLM, so even if the model returns 7 replacements the pipeline only applies 3 — saves downstream cost and preserves correctness.
- **Confidence-gated application** (`validators.py:51-56`, `cv_modifier.py:109`) — `is_applicable()` requires confidence ≥ 0.7. Prevents low-quality edits from being applied; a cheap quality filter that costs zero LLM tokens.
- **Prompt-injection wrappers** — `<untrusted_data label="…">` blocks (`prompts.py:31-34, 65-67, 73-75, 152-154`) plus `sanitize_for_prompt()` calls everywhere external data enters the prompt. Solid defence-in-depth.
- **`_strip_preamble`** (`cv_modifier.py:42-61`) — drops the LaTeX preamble before sending to the model. Saves 30-50 % of CV-source tokens with zero loss of editability. Smart.
- **Per-CV truncation cap at 50 KB** (`cv_modifier.py:93-95`) — protects against absurd CVs blowing the context window. Plus a warning log.
- **Sliding-window rate limiter** (`gemini_client.py:126-136`) — async-correct, never blocks > 120 s. Honors `Retry-After` header and Gemini-specific `retry_delay { seconds: N }` patterns (`gemini_client.py:41-83`).
- **Model-not-found failover** (`gemini_client.py:138-217`) — if the primary model is retired or unavailable, the client transparently moves to the next candidate. Good resilience.
- **Embedding skip-already-embedded** (`embedder.py:43-48, 67-71`) — within a single in-process run, embeddings are idempotent. The only missing piece is *cross-run* persistence (LL-04).
- **Job-context cache exists**, even if in-memory only (`pipeline.py:109, 191-204`) — TTL + size cap shows awareness of the redundant-call problem; the design just needs to extend across processes (LL-06).
- **CONCURRENCY_GEMINI = 3** (`defaults.py:37`) — sensible cap that keeps the 15 RPM headroom for retries.
- **`top_three()` confidence-sort + cap** (`validators.py:66-72`) — the LLM might overshoot; the boundary class enforces the contract.

---

## Notes on what's *not* in scope here

- Authentication / CORS / secrets typing — already in the standards backlog (`ST-01`, `ST-02`).
- `morning_batch` rename / scheduler scaffolding cleanup — `NM-01`, `DC-01`.
- `Optional[X]` vs `X | None` typing — `TY-06`.
- The `/regenerate` no-op endpoint — `RG-01` (correctness, not LLM efficiency).
- `concurrency_gemini` rename — `NM-04`.

These are tracked separately in `docs/reports/2026-05-22-standards/`.
