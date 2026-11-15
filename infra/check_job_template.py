"""Check the work pool's job template and the deployments in one rendered chart (task 5).

Called by infra/check-chart.sh:  uv run python infra/check_job_template.py RENDER IMAGE
RENDER is the output of `helm template`; IMAGE is the image the flow-run Jobs must use.
Prints one line per check; exits 1 on the first failure. No network, no cluster.
"""

from __future__ import annotations

import json
import re
import sys

import yaml


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def check(cond: bool, msg: str) -> None:
    if not cond:
        fail(msg)


def main(render_path: str, image: str) -> None:
    docs = [d for d in yaml.safe_load_all(open(render_path, encoding="utf-8")) if d]
    cms = {d["metadata"]["name"]: d for d in docs if d["kind"] == "ConfigMap"}
    check("prefect-config" in cms, "no prefect-config ConfigMap in the render")
    data = cms["prefect-config"]["data"]
    raw = data["base-job-template.json"]
    left = re.findall(r"@[A-Z_]+@", raw)
    check(not left, f"placeholders left unreplaced: {left}")
    tpl = json.loads(raw)
    variables = tpl["variables"]["properties"]
    job = tpl["job_configuration"]["job_manifest"]
    pod = job["spec"]["template"]["spec"]
    (c,) = pod["containers"]

    # Call 14: a flow-run Job never retries on its own, and a deployment cannot change that.
    check(job["spec"]["backoffLimit"] == 0, "backoffLimit is not a literal 0")
    check("backoff_limit" not in variables, "backoff_limit is still a job variable")
    check(pod["restartPolicy"] == "Never", "restartPolicy is not Never")
    print("ok   Job never retries: backoffLimit 0, not overridable; restartPolicy Never")

    # The image's entrypoint is `elusion`; `env` replaces it so the worker's args
    # (`prefect flow-run execute`) run as given.
    check(c["command"] == ["env"] and c["args"] == "{{ command }}", f"command/args: {c}")
    check(variables["image"]["default"] == image, f"image default {variables['image']}")
    check(variables["namespace"]["default"] == "elusion", "namespace default is not elusion")
    print(f"ok   flow runs use {image}, command overridden for the elusion entrypoint")

    env = c["env"]
    names = [e["name"] for e in env]
    by = {e["name"]: e for e in env}
    pg = by["POSTGRES_PASSWORD"]["valueFrom"]["secretKeyRef"]
    check(pg == {"name": "elusion-postgres", "key": "POSTGRES_PASSWORD"}, f"postgres ref {pg}")
    url = by["DATABASE_URL"]["value"]
    check("$(POSTGRES_PASSWORD)" in url, f"DATABASE_URL does not expand the password: {url}")
    check(names.index("POSTGRES_PASSWORD") < names.index("DATABASE_URL"), "env order")
    oa = by["OPENAI_API_KEY"]["valueFrom"]["secretKeyRef"]
    check(oa["name"] == "elusion-openai" and oa["key"] == "OPENAI_API_KEY", f"openai ref {oa}")
    check(c["envFrom"] == [{"configMapRef": {"name": "elusion-settings"}}], "envFrom")
    print("ok   env: ConfigMap, both Secrets by name, DATABASE_URL built from the password")

    vols = {v["name"]: v for v in pod["volumes"]}
    check(vols["corpus"]["persistentVolumeClaim"]["claimName"] == "corpus", "corpus volume")
    check({"name": "corpus", "mountPath": "/data"} in c["volumeMounts"], "corpus mount")
    sc = pod["securityContext"]
    check(sc.get("runAsNonRoot") is True and sc.get("runAsUser") == 10001, f"securityContext {sc}")
    check(pod.get("automountServiceAccountToken") is False, "the Job pod gets an API token")
    print("ok   corpus at /data, non-root, no API token in the flow-run pod")

    for v in ("cpu_request", "cpu_limit", "memory_request", "memory_limit"):
        check(variables[v].get("default"), f"{v} has no default")
    print("ok   requests and limits have defaults")

    # The deployments: plain-value parameters, module entrypoints, this work pool.
    deps = {d["name"]: d for d in yaml.safe_load(data["prefect.yaml"])["deployments"]}
    check(set(deps) == {"collect", "review"}, f"deployments {set(deps)}")
    check(deps["collect"]["entrypoint"] == "elusion.flows.collect.collect", "collect entrypoint")
    check(deps["review"]["entrypoint"] == "elusion.flows.review.review", "review entrypoint")
    for d in deps.values():
        check(d["work_pool"]["name"] == "elusion", f"{d['name']} work pool")
        jv = d["work_pool"].get("job_variables", {})
        check("backoff_limit" not in jv and "image" not in jv, f"{d['name']} overrides {jv}")
    print("ok   deployments collect and review on pool elusion, module entrypoints")


if __name__ == "__main__":
    main(*sys.argv[1:])
