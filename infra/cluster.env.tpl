# 1Password template for the AKS run (phase 6). Safe to commit: references, not values.
# Only the cluster's own OpenAI key: made for this run, fed into a Kubernetes Secret on the
# Azure day (see infra/README.md), revoked after `terraform destroy` (call 8). The local
# .env.tpl keeps pointing at the laptop's key.
OPENAI_API_KEY=op://dev-secrets/OpenAI elusion aks/api key
