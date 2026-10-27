"""Build the committed Pi artifact as an E2B template; no repository is embedded."""

import argparse
import json
import subprocess
from pathlib import Path

from e2b import Template, default_build_logger


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alias", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    context = Path(__file__).resolve().parent
    subprocess.run(
        ["git", "diff", "--exit-code", "HEAD", "--", str(context)],
        cwd=context,
        check=True,
        capture_output=True,
    )
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=context, text=True).strip()
    dockerfile = (context / "Dockerfile").read_text()
    # E2B snapshots this VM-owned supervisor into the template. Individual
    # commands.run bridges attach to it; they never own the file service.
    lines = [line for line in dockerfile.splitlines() if not line.startswith("ENTRYPOINT ")]
    template = (
        Template(file_context_path=context)
        .from_dockerfile("\n".join(lines))
        .set_start_cmd(
            "node /opt/puppyone-agent/workbench-files-supervisor.mjs",
            "test -r /opt/puppyone-agent/worker.mjs && test -w /workspace && test ! -e /recovery && "
            'node -e \'const s=require("net").connect("/run/puppyone-workbench/files.sock");'
            's.once("connect",()=>s.end());s.once("error",()=>process.exit(1));'
            "setTimeout(()=>process.exit(1),2000).unref();'",
        )
    )
    result = Template.build(
        template,
        alias=args.alias,
        cpu_count=2,
        memory_mb=2048,
        on_build_logs=default_build_logger(),
    )
    args.output.write_text(
        json.dumps(
            {
                "source_sha": sha,
                "alias": args.alias,
                "template_id": result.template_id,
                "build_id": result.build_id,
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
