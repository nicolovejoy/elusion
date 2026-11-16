# Phase 6: Production in Azure

<!-- Nico's draft. Brackets are mine to overwrite. -->

## What we built

We ran the champion reviewer (gpt-6-luna, prompt v2) once on AKS (Azure Kubernetes
Service): two `Standard_D2as_v7` nodes in West US 2, created by my `terraform apply` at
about 07:50 Pacific on 2026-11-16 and destroyed at 11:41, about 3 hours 50 minutes of
billing. It collected the 599,703 paragraphs filed in 2020 and reviewed the first 20,000.

- [Terraform: files that declare the Azure resources; `apply` makes Azure match them,
  `destroy` removes what they made.]
- [Helm: a package of Kubernetes manifests with values filled in per cluster. One chart
  ran Postgres, Redpanda and the Prefect server as pods, mirroring `compose.yaml`.]
- [Work pool: a queue on the Prefect server holding the job template, the recipe that
  turns a flow run into a Kubernetes Job (a pod that runs once to completion), retries
  off.]
- [Worker: a long-running pod that polls the work pool and creates a Job for each flow
  run it picks up.]

Azure to AWS, for each resource the module created or used:

- resource group `rg-elusion`: [no direct equivalent? a CloudFormation stack?]
- AKS, free control-plane tier: EKS
- node resource group `MC_rg-elusion_aks-elusion_westus2`, made by AKS for the nodes,
  disks and networking: [no equivalent? EKS nodes sit in the account itself]
- the two node VMs: EC2 instances in a managed node group [m7a.large?]
- OS disks, and the Postgres, Redpanda and Prefect volumes on the default storage class:
  EBS [is that class a managed disk?]
- outbound load balancer and public IP: [NAT gateway and Elastic IP?]
- [the overlay network's virtual network: a VPC?]
- container registry, Basic: ECR
- storage account and its private `corpus` container: S3 [container = bucket?]
- Blob CSI driver (blobfuse2), mounting the container in pods: [Mountpoint for S3?]
- the cluster's control-plane identity: the EKS cluster IAM role
- the kubelet's (node agent's) managed identity: the node IAM role
- three role assignments (AcrPull, and Storage Blob Data Contributor for the kubelet and
  for me): IAM policies on roles
- two $10 budgets, one per resource group: AWS Budgets

## Why we built it

Everything until now ran from `compose.yaml` on one laptop. We wanted to see what changes
on machines rented by the hour, declared in files and removed the same day, with the
hard rules (no paying twice, the spend guard, no secrets in git) still
holding. [My reason, in my words.]

## What we learned

Two rehearsals on OrbStack's local Kubernetes caught one fault before paid time. Prefect's Kubernetes integration reschedules a killed run, so
the worker would have started a second Job and scored further documents; the work pool now defaults to `crash`. The whole-year `collect` peaked at 3.49 GiB
under its 4Gi limit, raised to 5Gi for margin.

What broke was the one thing OrbStack could not rehearse: the blob mount. The first
review failed on batch 1 with `50 ids not in the Parquet corpus`. The driver adds
`--cancel-list-on-mount-seconds=10`, and for that long after a mount a directory listing
comes back empty; the review listed `/data/corpus` 7.2 seconds after mounting. No model
call, $0.00. Setting it to `0` in `values-aks.yaml` and a `helm upgrade` fixed it. [What
this says about rehearsals.]

Expected cost: Azure about $2 for an 8-hour day, review about
$2.20, judge about $1, grade about $0.20, about $5.50. Model spend: review $2.1941, judge
$0.695124, grade $0.272924, $3.16 in all against about $3.40.

[Azure bill for rg-elusion, read 2026-11-17: $__]
[Azure bill for MC_rg-elusion_aks-elusion_westus2, read 2026-11-17: $__]

## Results

The population is the first 20,000 of the 599,703 paragraphs filed in 2020, in ingestion
order, not a random draw; the figures describe those 20,000 only. The truth is the Opus
judge, which matched my 2018-2019 labels on 16 of 20. Precision is the fraction of flagged
paragraphs that are truly relevant; elusion is the relevant rate inside the discard pile.

- precision: 0.485 [0.360, 0.611] (32/66) for 2020; 0.511 [0.358, 0.663] (23/45) for
  2018-2019
- elusion: 0.000 [0.000, 0.018] (0/200) for both
- review: one Job, wall-clock 1:46:44, 187 documents a minute, $2.1941
- export: 20,000 `scores` rows and 3 `review_jobs` rows, each equal to the cluster's
  count (the 3 include the failed start and a `sample=0` run)

The two precision intervals overlap almost entirely. Recall (the fraction of truly
relevant paragraphs the system flagged) reads 1.000 [0.061, 1.000]. The point value is
an artefact: the discard sample held no relevant paragraph, so the estimate assumes none
were missed. Only the interval informs, and 0.061 rules out little. Richness (the
fraction of the whole corpus that is relevant), here of the 20,000, is 0.002 [0.001, 0.020]. Both are derived
from the two pile intervals, so each covers the true value with probability at least
about 90%.
