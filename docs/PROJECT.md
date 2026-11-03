# Project goals

## Why this exists

To learn, by building, the tools and vocabulary of production ML platforms, specifically
the ones named in e-discovery AI engineering roles: orchestration (Prefect, with Airflow
understood), experiment tracking and model registry (MLflow), distributed batch (Spark,
Delta Lake), Kubernetes on Azure (AKS, Helm, Terraform), and Pulumi as a Terraform
alternative. The owner has deep systems experience and recent LLM application experience
on AWS; the gaps are the ML-platform layer and Azure.

The project is deliberately small and deliberately real: one job, done the way a
defensible review has to be done, rather than a tour of tools.

## The matter

A regulatory sweep. Every public company's annual 10-K has an "Item 1A: Risk Factors"
section. Split those sections into paragraphs, treat each paragraph as a document, and
find every paragraph that matches a written criterion, for example:

> Discloses reliance on a single cloud or hosting provider, or on a small number of
> providers, as a material risk.

The output is the set of matching paragraphs, the companies behind them, and a validation
report that says how much was found and how sure we are.

## What gets built

1. **Collect**: download EDGAR-CORPUS for three filing years, split Item 1A into
   paragraphs, write Parquet, load metadata into Postgres, publish document ids to a
   Kafka topic.
2. **Review**: a Prefect flow that consumes the topic in batches, sends each paragraph
   to an LLM with a pinned model snapshot and a versioned prompt, and stores a score
   (0–4), a rationale, and the quoted sentence. Idempotent, resumable, rate-limit aware,
   with a cost guard.
3. **Evaluate**: a hand-labeled gold set of ~300 paragraphs; recall, precision, elusion
   and richness with Clopper-Pearson 95% intervals; a one-page report per run.
4. **Leaderboard**: every run logged to MLflow with its parameters, metrics and cost;
   a second provider and a local model for comparison; the winner registered and
   promoted in the model registry; an LLM-as-judge pass over rationales.
5. **Dedupe (optional)**: a PySpark MinHash job finding boilerplate risk language reused
   across companies and years, written as a Delta table, so review scores one
   representative per cluster.
6. **Production**: Terraform stands up a small AKS cluster; a Helm chart deploys the
   review service and a Prefect worker; one real run happens there; everything is torn
   down the same day. Optional Pulumi rewrite of the Terraform module.

## Done means

- `docker compose up` brings the system up locally.
- One command runs a review end to end and writes a report.
- MLflow shows at least five compared runs, one promoted.
- Terraform stands the same thing up in Azure and tears it down, for under $10.
- `docs/notes/` has one note per completed phase.

## Not goals

- A product, a UI, or multi-tenancy.
- Fine-tuning any model.
- Covering every filing year; three is enough to feel scale.
- Spending more than about $40 in total.

## Cuts, in order, if time runs short

Pulumi rewrite, then phase 5, then the local Ollama model. Never the gold set or the
report.
