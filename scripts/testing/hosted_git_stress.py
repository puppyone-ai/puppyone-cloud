"""Strict, owned Git consistency checks using a fresh human login per run."""

from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx


class HostedCheckFailure(RuntimeError):
    """Only fixed check names and HTTP status codes; safe to report in CI."""


def prepare_account(raw: str, destination: Path) -> None:
    config = json.loads(raw)
    account = config.get("acceptance") or {}
    keys = ("api_url", "org_id", "email", "password")
    if not all(isinstance(account.get(key), str) and account[key] for key in keys):
        raise ValueError("Configure the protected acceptance account")
    # Do not pass transformed JSON credentials through an Actions env expression:
    # derived substrings are not covered by the original secret's log masking.
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump({key: account[key] for key in keys}, output)


def run(account: dict, iterations: int, *, api_url: str = "", transport=None) -> dict:
    if type(iterations) is not int or not 1 <= iterations <= 100:
        raise ValueError("Stress iterations must be 1..100")
    if not all(isinstance(account.get(key), str) and account[key]
               for key in ("api_url", "org_id", "email", "password")):
        raise ValueError("Configure the protected acceptance account")
    expected = account["api_url"].rstrip("/")
    parsed = urlsplit(expected)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path
            or (api_url and api_url.rstrip("/") != expected)):
        raise ValueError("Stress URL must match the protected acceptance account")

    with httpx.Client(base_url=expected, timeout=45, trust_env=False,
                      transport=transport, follow_redirects=False) as client:
        client.headers["X-PuppyOne-Repository-Contract"] = "2"

        def call(method, path, *, check, statuses=(200,), **kwargs):
            response = client.request(method, "/api/v1" + path, **kwargs)
            if response.status_code not in statuses:
                raise HostedCheckFailure(f"{check}: HTTP {response.status_code}")
            if not response.content:
                return {}
            body = response.json()
            return body.get("data", body) if isinstance(body, dict) else body

        login = call("POST", "/auth/login", check="login", json={
            "email": account["email"], "password": account["password"],
        })
        if not isinstance(login.get("access_token"), str) or not login["access_token"]:
            raise HostedCheckFailure("login: no session")
        client.headers["Authorization"] = "Bearer " + login["access_token"]
        nonce = uuid4().hex
        project = call("POST", "/projects/", statuses=(200, 201),
                       check="create-project",
                       headers={"Idempotency-Key": nonce},
                       json={"name": "Hosted Git stress " + nonce[:12],
                             "org_id": account["org_id"]})
        if not isinstance(project.get("id"), str) or not project["id"]:
            raise HostedCheckFailure("create-project: no owned project")
        project_id = project["id"]
        project_path = "/projects/" + quote(project_id, safe="")
        content_path = "/content/" + quote(project_id, safe="")
        completed = 0
        try:
            surfaces = call("POST", project_path + "/access/surfaces/enable-target", check="enable-access", json={
                "target": {"kind": "project_root", "project_id": project_id},
            })
            if not {"git_remote", "cli"} <= {row.get("kind") for row in surfaces}:
                raise HostedCheckFailure("enable-access: missing entry points")
            for iteration in range(iterations):
                files = [{"path": f"stress-{nonce}-{iteration}-{index}.md",
                          "content": f"{nonce}:{iteration}:{index}", "node_type": "markdown"}
                         for index in range(3)]
                call("POST", content_path + "/bulk-write", statuses=(200, 201),
                     check="bulk-write",
                     json={"files": files})
                renamed = "renamed-" + files[0]["path"]
                call("POST", content_path + "/mv", statuses=(200, 201),
                     check="move",
                     json={"old_path": files[0]["path"], "new_path": renamed})
                body = call("GET", content_path + "/cat", check="read", params={"path": renamed})
                if body.get("content_text") != files[0]["content"]:
                    raise HostedCheckFailure("read: acknowledged content mismatch")
                health = call("GET", project_path + "/git-view/health", check="git-health")
                if health.get("health") not in {"healthy", "history_degraded"} or health.get("git_usable") is not True:
                    raise HostedCheckFailure("git-health: repository unusable after move")
                completed += 1
        finally:
            call("DELETE", project_path, check="cleanup", statuses=(200, 202, 204))
    return {"iterations": iterations, "passed_iterations": completed,
            "git_read_after_move": True, "cleanup_requested": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=30)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare-account", type=Path)
    mode.add_argument("--account-file", type=Path)
    args = parser.parse_args()
    if args.prepare_account:
        prepare_account(os.environ.get("RELEASE_CONFIG_JSON", ""), args.prepare_account)
        print("Protected smoke account prepared")
        return
    metadata = args.account_file.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
        raise ValueError("The smoke account must be a private regular file")
    account = json.loads(args.account_file.read_text())
    result = run(account, args.iterations, api_url=os.environ.get("PUPPYONE_API_URL", ""))
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except HostedCheckFailure as error:
        raise SystemExit(f"Hosted Git stress failed ({error})") from None
    except (ValueError, RuntimeError, OSError, httpx.HTTPError) as error:
        # Never echo passwords, bearer tokens, response bodies or customer rows.
        raise SystemExit(f"Hosted Git stress failed ({type(error).__name__})") from None
