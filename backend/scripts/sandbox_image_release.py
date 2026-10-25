"""Publish the hosted sandbox image and pin it for new workspaces.

Run by the environment release workflow after the image was built locally as
``--image``. Steps, each idempotent:

1. Read the runtime manifest inside the image and refuse it unless its runtime
   and file protocols and capabilities match what this backend expects.
2. Tag it by content (``build-<build_id>``) in the Fly registry; an existing tag
   is reused, never overwritten.
3. Pin ``CLOUD_AGENT_FLY_IMAGE`` (``registry@sha256:...``) on the configured
   Railway services with ``skipDeploys``. The next application deployment
   applies it. Existing workspaces keep the artifact they were bound to.

Pinning needs ``RAILWAY_TOKEN`` (a project token scoped to one environment).
Without it the image is still verified and published, and the summary states
that nothing was pinned. Standard library only; no secrets are printed.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

RAILWAY_API = "https://backboard.railway.com/graphql/v2"
MANIFEST = "/opt/puppyone-agent/runtime-manifest.json"
CONTRACT = (
    Path(__file__).resolve().parents[1] / "src/platform/workspace/execution/runtime_contract.py"
)
DIGEST = re.compile(r"sha256:[a-f0-9]{64}")


class ReleaseError(RuntimeError):
    pass


def expected_contract(path: Path = CONTRACT) -> dict:
    """Protocol constants from the backend source, without importing the app."""
    values = {}
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            name = getattr(node.targets[0], "id", None)
            if name in {"RUNTIME_PROTOCOL", "FILE_PROTOCOL", "REQUIRED_CAPABILITIES"}:
                values[name] = ast.literal_eval(node.value)
    if set(values) != {"RUNTIME_PROTOCOL", "FILE_PROTOCOL", "REQUIRED_CAPABILITIES"}:
        raise ReleaseError("Runtime contract constants not found")
    return values


def check_manifest(manifest: dict, contract: dict) -> str:
    """Return the build id when the image implements this backend's contract."""
    build_id = manifest.get("build_id")
    if not isinstance(build_id, str) or not re.fullmatch(r"[a-f0-9]{64}", build_id):
        raise ReleaseError("Image runtime manifest has no valid build_id")
    if manifest.get("runtime_protocol") != contract["RUNTIME_PROTOCOL"]:
        raise ReleaseError("Image runtime protocol differs from the backend")
    if manifest.get("file_protocol") != contract["FILE_PROTOCOL"]:
        raise ReleaseError("Image file protocol differs from the backend")
    missing = set(contract["REQUIRED_CAPABILITIES"]) - set(manifest.get("capabilities") or [])
    if missing:
        raise ReleaseError("Image lacks required capabilities: " + ", ".join(sorted(missing)))
    return build_id


def run(*command: str, input_text: str | None = None) -> str:
    result = subprocess.run(command, input=input_text, capture_output=True, text=True, timeout=1800)
    if result.returncode:
        raise ReleaseError(f"{command[0]} {command[1]} failed: {result.stderr.strip()[-300:]}")
    return result.stdout


def read_manifest(image: str) -> dict:
    return json.loads(
        run("docker", "run", "--rm", "--network=none", "--entrypoint", "cat", image, MANIFEST)
    )


def remote_digest(reference: str) -> str | None:
    result = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", reference, "--format", "{{json .Manifest}}"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode:
        return None
    digest = json.loads(result.stdout).get("digest", "")
    return digest if DIGEST.fullmatch(digest) else None


def publish(image: str, repository: str, build_id: str) -> tuple[str, bool]:
    """Return (pinned reference, newly pushed)."""
    tag = f"{repository}:build-{build_id}"
    existing = remote_digest(tag)
    if existing:
        return f"{repository}@{existing}", False
    run("docker", "tag", image, tag)
    run("docker", "push", tag)
    pushed = remote_digest(tag)
    if not pushed:
        raise ReleaseError("Pushed image digest could not be resolved")
    return f"{repository}@{pushed}", True


class Railway:
    def __init__(self, token: str, project: str, environment: str, opener=urllib.request.urlopen):
        self.token, self.project, self.environment, self.opener = (
            token,
            project,
            environment,
            opener,
        )

    def query(self, query: str, variables: dict) -> dict:
        request = urllib.request.Request(
            RAILWAY_API,
            data=json.dumps({"query": query, "variables": variables}).encode(),
            headers={
                "Project-Access-Token": self.token,
                "Content-Type": "application/json",
                "User-Agent": "puppyone-sandbox-image-release",
            },
        )
        try:
            body = json.loads(self.opener(request, timeout=60).read())
        except urllib.error.HTTPError as error:
            raise ReleaseError(f"Railway API HTTP {error.code}") from error
        if body.get("errors"):
            raise ReleaseError("Railway API rejected the request")
        return body["data"]

    def pinned(self, service: str) -> str | None:
        data = self.query(
            "query($p:String!,$e:String!,$s:String!){variables(projectId:$p,environmentId:$e,serviceId:$s)}",
            {"p": self.project, "e": self.environment, "s": service},
        )
        return (data.get("variables") or {}).get("CLOUD_AGENT_FLY_IMAGE")

    def pin(self, service: str, reference: str) -> None:
        self.query(
            "mutation($i:VariableCollectionUpsertInput!){variableCollectionUpsert(input:$i)}",
            {
                "i": {
                    "projectId": self.project,
                    "environmentId": self.environment,
                    "serviceId": service,
                    "skipDeploys": True,
                    "variables": {"CLOUD_AGENT_FLY_IMAGE": reference},
                }
            },
        )


def pin_all(railway: Railway | None, services: list[str], reference: str) -> dict:
    if railway is None:
        return {"state": "not_configured", "services": {}}
    result = {}
    for service in services:
        previous = railway.pinned(service)
        if previous == reference:
            result[service] = {"changed": False}
            continue
        railway.pin(service, reference)
        if railway.pinned(service) != reference:
            raise ReleaseError("Railway did not store the pinned image")
        result[service] = {"changed": True, "previous": previous}
    return {"state": "pinned", "services": result}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--image", required=True, help="locally built image reference")
    parser.add_argument("--app", required=True, help="Fly app owning the registry repository")
    parser.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY"))
    args = parser.parse_args(argv)

    contract = expected_contract()
    build_id = check_manifest(read_manifest(args.image), contract)
    reference, pushed = publish(args.image, f"registry.fly.io/{args.app}", build_id)
    token = os.environ.get("RAILWAY_TOKEN", "").strip()
    services = [
        s.strip()
        for s in os.environ.get("RAILWAY_SANDBOX_IMAGE_SERVICES", "").split(",")
        if s.strip()
    ]
    railway = None
    if token and services:
        railway = Railway(
            token, os.environ["RAILWAY_PROJECT_ID"], os.environ["RAILWAY_ENVIRONMENT_ID"]
        )
    pins = pin_all(railway, services, reference)
    report = {"build_id": build_id, "image": reference, "pushed": pushed, "pin": pins}
    print(json.dumps(report, indent=2))
    if args.summary:
        lines = [
            "### Sandbox image",
            f"- build `{build_id[:16]}`",
            f"- `{reference}` ({'pushed' if pushed else 'already published'})",
        ]
        if pins["state"] == "not_configured":
            lines.append(
                "- **Not pinned**: configure `RAILWAY_TOKEN` and `RAILWAY_SANDBOX_IMAGE_SERVICES` in this environment."
            )
        else:
            changed = sum(1 for value in pins["services"].values() if value["changed"])
            lines.append(
                f"- pinned on {len(pins['services'])} service(s), {changed} changed; applied by the next deployment"
            )
        with open(args.summary, "a") as output:
            output.write("\n".join(lines) + "\n")
    if pins["state"] == "not_configured":
        print(
            "::warning::Sandbox image published but not pinned: RAILWAY_TOKEN is not configured",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReleaseError as error:
        print(f"::error::{error}", file=sys.stderr)
        raise SystemExit(1) from None
