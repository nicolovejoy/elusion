# 1Password template. Materialize with:  op inject -i .env.tpl -o .env
# or run without a file:                  op run --env-file=.env.tpl -- uv run elusion ...
# Edit the secret references (vault/item/field) to match your 1Password names. Safe to commit.

# LLM providers
ANTHROPIC_API_KEY=op://Private/Anthropic elusion/credential
OPENAI_API_KEY=                       # optional, phase 4
OLLAMA_HOST=http://localhost:11434    # optional, phase 4

# Pinned models (change only via a DECISIONS.md line)
ELUSION_REVIEW_MODEL=claude-haiku-5-5
ELUSION_JUDGE_MODEL=claude-opus-5-5

# Spend guard
ELUSION_MAX_RUN_USD=10

# Collection
ELUSION_DATA_DIR=data
ELUSION_SEC_USER_AGENT=

# Local services (match compose.yaml)
DATABASE_URL=postgresql://elusion:elusion@localhost:5433/elusion
KAFKA_BOOTSTRAP=localhost:9092
MLFLOW_TRACKING_URI=http://localhost:5001
PREFECT_API_URL=http://localhost:4200/api
