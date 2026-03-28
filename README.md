# Job Ad Normalizer (POC)

A local-first and CI-ready pipeline that converts messy job ads into clean, structured, versioned markdown using an LLM.

## Structure

```text
jobs/
  raw/        # input job ads
  processed/  # generated structured outputs
src/
  prompt.md   # extraction instructions
  run.py      # local/CI runner
.github/workflows/main.yml
```

The sample job ad is fully synthetic and intentionally noisy (duplicates, UI text, etc.) to simulate real copy-paste from job platforms.

## Local usage

Activate env and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install openai
pip freeze > requirements.txt
```

Set your API key and run the processor:

```bash
export OPENAI_API_KEY=your_key_here
python3 src/run.py jobs/raw/job1.md
```

Process multiple specific files:

```bash
python3 src/run.py jobs/raw/job1.md jobs/raw/job2.md
```

Optional environment variables:

- `OPENAI_MODEL` to override the default model (`gpt-5.4-mini`)
- `OPENAI_BASE_URL` to point to a compatible API base URL

Each generated file is written to `jobs/processed/` and contains:

- source metadata
- processing timestamp
- model name
- deterministic structured JSON

The markdown is generated from the structured JSON, which is the source of truth.
Output filenames mirror the input stem, for example `jobs/raw/job1.txt` becomes `jobs/processed/job1.md`.
Reprocessing the same input overwrites that file.
If no input files are passed to `src/run.py`, nothing is processed.

## CI behavior

The GitHub Actions workflow:

- triggers on pushes affecting `jobs/raw/**`
- can also be started manually with `workflow_dispatch`
- detects changed raw files
- ensures only newly added or modified job ads are processed, avoiding unnecessary API calls
- runs `src/run.py` once per changed file
- commits generated files in `jobs/processed/` back to the repository

To enable CI, add `OPENAI_API_KEY` as a GitHub Actions secret.

## Design principles

- JSON is the source of truth
- Markdown is generated for readability
- One input file → one deterministic output file
- No database, no UI, local-first workflow
- CI handles orchestration, not business logic
