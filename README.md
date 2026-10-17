# llm-judge-calibration

LLM-as-judge for Project 1's RAG answers, calibrated against real human ratings (HelpSteer2), tested
for position, length and self-preference bias, and wired into an evidence-based CI release gate.

Draft: experiments are still finishing under the Groq free-tier quota. Every number in the final
README comes from a committed raw file; anything not run says "not yet measured".

## Setup

```bash
uv sync --frozen
cp .env.example .env                       # set POSTGRES_PASSWORD
docker compose -p p4-judge up -d --build   # postgres 127.0.0.1:15433, UI 127.0.0.1:8504
```
