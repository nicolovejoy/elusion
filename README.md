# elusion

A miniature document-review pipeline over public SEC 10-K risk-factor paragraphs, built
to learn the MLOps toolchain (Prefect, MLflow, Spark, AKS, Pulumi) by doing one realistic
job end to end: find the paragraphs that match a criterion, and prove, with recall,
precision and elusion plus confidence intervals, that enough of them were found.

**Elusion** is the e-discovery metric for what a review missed: the relevant-document rate
inside the pile the system discarded, measured by sampling that pile. It is the number
opposing counsel attacks, and the number this project exists to produce honestly.

## Status

Phase 0: scaffolding. See `docs/PHASES.md`.

## Run it

```
cp .env.example .env     # add your Anthropic key
docker compose up -d     # postgres (:5433), redpanda, mlflow (:5001), prefect (:4200)
uv sync
uv run elusion --help
```

## Layout

See `CLAUDE.md` for the directory layout and working rules, `docs/PROJECT.md` for goals,
and `docs/notes/` for what was learned in each phase.

## Data

[EDGAR-CORPUS](https://huggingface.co/datasets/eloukas/edgar-corpus) on HuggingFace:
every 10-K filing 1993–2020 with each item pre-extracted. This project uses Item 1A
(Risk Factors) for a few filing years, split into paragraphs. Public, no account needed.

## License

MIT.
