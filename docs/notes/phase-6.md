# Phase 6: Production in Azure

<!-- Nico's draft. The Azure bill was filled in on 2026-11-20. -->

## What we built

We ran the champion reviewer (gpt-6-luna, prompt v2) once on AKS (Azure Kubernetes
Service): two `Standard_D2as_v7` nodes in West US 2, created by my `terraform apply` at
about 07:50 Pacific on 2026-11-16 and destroyed at 11:41, about 3 hours 50 minutes of
billing. It collected the 599,703 paragraphs filed in 2020 and reviewed the first 20,000.

Four tools did the work:

- **Terraform** declared the Azure resources in files. `apply` made Azure match the
  files; `destroy` removed everything they had made.
- **Helm** installed the software into the cluster. A chart is a package of Kubernetes
  manifests with values filled in per cluster; ours ran Postgres, Redpanda and the
  Prefect server as pods, mirroring `compose.yaml`.
- **The work pool** is a queue on the Prefect server. It holds the job template: the
  recipe that turns one flow run into one Kubernetes Job (a pod that runs once, to
  completion), with retries off.
- **The worker** is a long-running pod that polls the work pool and creates a Job for
  each flow run it picks up.

What Terraform made or used in Azure, and the AWS counterpart of each:

| Azure | AWS | Note |
|---|---|---|
| Resource group `rg-elusion` | none; nearest is a CloudFormation stack | Every Azure resource lives in exactly one group, and deleting the group deletes them all |
| AKS, free control-plane tier | EKS | EKS has no free tier for the control plane |
| Node resource group `MC_rg-elusion_aks-elusion_westus2` | none | AKS makes this second group itself for the nodes, disks and networking; EKS puts those straight into the account |
| Two node VMs, `Standard_D2as_v7` | EC2 instances in a managed node group | 2 vCPU, 8 GB, AMD: the `m7a.large` class |
| OS disks and the Postgres, Redpanda and Prefect volumes (managed disks) | EBS volumes | The cluster's default storage class |
| Virtual network | VPC | Pods get addresses from an overlay range, not from the network itself |
| Outbound load balancer and public IP | NAT gateway and Elastic IP | Used only for traffic leaving the cluster; nothing is exposed |
| Container registry, Basic | ECR | |
| Storage account | none | The account-level wrapper around containers |
| Its private `corpus` container | S3 bucket | Holds the Parquet file |
| Blob CSI driver (blobfuse2) | Mountpoint for Amazon S3 CSI driver | Mounts the container in a pod as a directory |
| Cluster's control-plane identity | EKS cluster IAM role | |
| Kubelet's (node agent's) managed identity | Node IAM role | Pulls images and reads the corpus, with no key |
| Three role assignments | IAM policies attached to roles | AcrPull, and Storage Blob Data Contributor for the kubelet and for me |
| Two $10 budgets, one per resource group | AWS Budgets | |

## Why we built it

Everything until now ran from `compose.yaml` on one laptop. We wanted to see what changes
on machines rented by the hour, declared in files and removed the same day, with the
hard rules (no paying twice, the spend guard, no secrets in git) still holding. IaC with
Terraform is familiar to me, but Azure's all new, and this was a quick way to dip my toe
in and kill two birds, in a way.

## What we learned

Rehearsing on OrbStack's local Kubernetes was a good way to learn. It changed the chart
and the runbook, not the Azure resources. It showed that Prefect restarts a killed run by
itself, which would have scored and paid for documents nobody asked for; the work pool
now marks such a run crashed instead. It also showed the whole-year `collect` peaking at
3.49 GiB under a 4Gi limit, which we raised to 5Gi for margin. The one Azure choice that
changed, the node size, changed for another reason: the subscription had a quota of 0 for
the size we first planned.

What broke on Azure was the one thing OrbStack could not rehearse, the blob mount. For 10
seconds after a mount the driver returns an empty directory listing (its default
`--cancel-list-on-mount-seconds=10`). The review listed the corpus 7.2 seconds in, found
no text and failed on its first batch, before any model call, at $0.00. One line in
`values-aks.yaml` and a `helm upgrade` fixed it. A rehearsal proves only what its
environment shares with the real one.

Model spend was $3.16 against a projection of about $3.40: review $2.19 (projected
$2.20), judge $0.70 (about $1), grade $0.27 (about $0.20). Azure was projected at about
$2 for an 8-hour day, and the cluster ran for under four.

Azure bill, read from Cost Management on 2026-11-20: $0.93 in all. $0.03 for
`rg-elusion` (the container registry) and $0.90 for `MC_rg-elusion_aks-elusion_westus2`
(virtual machines $0.56, their disks $0.20, the load balancer $0.12, the virtual network
$0.02). Only `NetworkWatcherRG` is left in the subscription, and it costs nothing.

## Results

The population is the first 20,000 of the 599,703 paragraphs filed in 2020, in ingestion
order, not a random draw; the figures describe those 20,000 only. The truth is the Opus
judge, which matched my 2018-2019 labels on 16 of 20.

In plain terms: the reviewer flagged 66 of the 20,000 paragraphs, and the judge agreed on
32 of them. About half of what it flags is worth a reader's time, and that held on a new
filing year (23 of 45 on 2018-2019). To see what it missed, the judge read 200 paragraphs
drawn at random from the 19,934 it discarded and found none relevant. That is what
missing nothing would look like, but 200 is too few to prove it: the same result is
expected when as much as 1.8% of the discard pile is relevant, about 360 paragraphs,
eleven times the 32 we found. So we cannot say we surfaced every relevant paragraph. We
can say we looked for a miss and found no sign of one.

- precision (the fraction of flagged paragraphs that are truly relevant): 0.485
  [0.360, 0.611] (32/66) for 2020; 0.511 [0.358, 0.663] (23/45) for 2018-2019
- elusion (the relevant rate inside the discard pile, measured by sampling it): 0.000
  [0.000, 0.018] (0/200) for both
- review: one Job, wall-clock 1:46:44, 187 documents a minute, $2.1941
- export: 20,000 `scores` rows and 3 `review_jobs` rows, each equal to the cluster's
  count (the 3 include the failed start and a `sample=0` run)

The two precision intervals overlap almost entirely. Recall (the fraction of truly
relevant paragraphs the system flagged) reads 1.000 [0.061, 1.000]. The point value is
an artefact: the discard sample held no relevant paragraph, so the estimate assumes none
were missed. Only the interval informs, and 0.061 rules out little. Richness (the
fraction of the whole corpus that is relevant), here of the 20,000, is 0.002
[0.001, 0.020]. Both are derived from the two pile intervals, so each covers the true
value with probability at least about 90%.
