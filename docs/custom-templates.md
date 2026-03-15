# Custom CV Templates

This guide explains how to use your own LaTeX CV template with JobPilot and what makes a template compatible with the AI tailoring pipeline.

## Quick Start

1. Place your `.tex` file in `data/templates/`
2. If your template uses a custom document class (`.cls`) or packages (`.sty`), place those files alongside it
3. Go to **Settings > Profile** and set the **Base CV Path** to your file, or let JobPilot auto-detect it (it picks the first `.tex` file alphabetically)

## How the Pipeline Works

When AI tailoring is enabled, JobPilot:

1. **Copies** your template + support files (`.cls`, `.sty`, images) to an output directory
2. **Reads** the full LaTeX source as plain text
3. **Sends** the text to the AI along with the job posting
4. **Receives** up to 3 targeted text replacements (find-and-replace in the source)
5. **Validates** each replacement (original text must exist, no new LaTeX commands, confidence >= 0.7)
6. **Compiles** the modified `.tex` to PDF using Tectonic

## Compatibility Requirements

### Must Have

- **Valid LaTeX that compiles with Tectonic.** Test locally: `tectonic your_cv.tex`
- **Self-contained directory.** All files your template needs (`.cls`, `.sty`, fonts, images) must be in the same directory as the `.tex` file. Tectonic auto-downloads standard packages, but custom ones must be bundled.
- **Stable text anchors.** The AI modifier works by finding exact substrings in your source and replacing them. If your CV content is generated dynamically (e.g., from YAML data files or Lua scripts), the modifier won't be able to locate text to replace.

### Recommended

- **A clear Profile/Summary section.** The AI primarily edits this section to align your pitch with the job. If you don't have one, edits will be limited to the Skills section.
- **A Skills section with comma-separated or tabular skills.** The AI reorders skills to prioritize job-relevant ones and may add one missing skill if evidence exists in your experience.
- **Plain text content (not hidden in macros).** Content inside custom macros like `\skill{Python}` is harder for the AI to locate and replace safely. Prefer plain text or standard LaTeX commands.

### What the AI Will NOT Touch

- Experience section (job titles, dates, bullet points)
- Education section (degrees, institutions, dates)
- Certifications and their dates
- Company names, grades, GPAs
- Any LaTeX structural commands

### What the AI MAY Edit

| Section | Type of Edit |
|---------|-------------|
| Profile/Summary | Small rephrasing to align with job requirements |
| Skills | Reorder to prioritize relevant skills, add one supported skill |
| Additional Information | Minor adjustments to highlight relevant details |

## Optional: JOBPILOT Markers

You can add comment markers to explicitly define editable regions. This is optional — the AI works without them — but markers help the cover letter pipeline identify sections precisely.

```latex
% --- JOBPILOT:SUMMARY:START ---
Your summary paragraph here.
% --- JOBPILOT:SUMMARY:END ---

% --- JOBPILOT:EXPERIENCE:START ---
\begin{itemize}
  \item Built distributed systems...
\end{itemize}
% --- JOBPILOT:EXPERIENCE:END ---

% --- JOBPILOT:LETTER:PARA:START ---
Customizable motivation letter paragraph.
% --- JOBPILOT:LETTER:PARA:END ---
```

Supported marker names: `SUMMARY`, `EXPERIENCE`, `LETTER:PARA`

You can validate your markers via the API:

```
POST /api/documents/validate-template
Content-Type: application/json

{"tex_content": "...your LaTeX source..."}
```

Returns `{"has_markers": true/false, "warnings": [...]}`.

## Using the Default Document Class

JobPilot ships with a `resume.cls` document class at `scripts/defaults/templates/resume.cls`. You can use it with `\documentclass{resume}` or bring your own. If you use a different document class, just include the `.cls` file in your templates directory.

## Disabling AI Tailoring

If you hit the daily AI call limit or prefer to always use your base CV unchanged, go to **Settings > Search Preferences** and turn off the **AI CV Tailoring** toggle. Your CV will still be compiled to PDF for each job — it just won't be modified by the AI.

## Supported File Types in the Template Directory

The pipeline copies these file types alongside your `.tex`:

| Extension | Purpose |
|-----------|---------|
| `.cls` | Document class |
| `.sty` | LaTeX package |
| `.jpg`, `.jpeg`, `.png` | Images (e.g., profile photo) |
| `.pdf`, `.eps` | Vector graphics / embedded PDFs |

## Troubleshooting

| Problem | Solution |
|---------|----------|
| PDF not generated | Run `tectonic your_cv.tex` locally to check for compilation errors |
| AI changes not applied | Check the diff view — changes may have been rejected by validation (low confidence or text not found) |
| "No base CV path configured" | Place a `.tex` file in `data/templates/` or set the path in Settings > Profile |
| Custom fonts not found | Bundle font files in the template directory or use standard LaTeX fonts |
