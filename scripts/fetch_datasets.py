"""Download the public human-rated datasets used for calibration and bias tests.

- nvidia/HelpSteer2 (CC-BY-4.0): per-response human ratings 0-4 on helpfulness, correctness,
  coherence, complexity, verbosity; `disagreements/` holds the individual annotator ratings.
- lmsys/mt_bench_human_judgments (CC-BY-4.0): human pairwise verdicts on MT-bench answers.

Files go to data/raw/ (gitignored). The resolved commit sha of each dataset is written to
data/datasets_manifest.json (committed) so the exact revision is reproducible.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from judgecal.config import load_env  # noqa: E402

RAW = ROOT / "data" / "raw"
FILES = {
    "nvidia/HelpSteer2": ["train.jsonl.gz", "validation.jsonl.gz", "disagreements/disagreements.jsonl.gz"],
    "lmsys/mt_bench_human_judgments": [
        "data/human-00000-of-00001-25f4910818759289.parquet",
    ],
}
ALLOWED_HOSTS = {"huggingface.co", "cdn-lfs.huggingface.co", "cdn-lfs-us-1.huggingface.co", "cas-bridge.xethub.hf.co"}


def main() -> None:
    load_env()
    token = os.environ.get("HF_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    RAW.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict[str, object]] = {}
    with httpx.Client(timeout=120, headers=headers, follow_redirects=True) as http:
        for repo, files in FILES.items():
            info = http.get(f"https://huggingface.co/api/datasets/{repo}").json()
            sha = info["sha"]
            license_ = (info.get("cardData") or {}).get("license")
            entry: dict[str, object] = {"revision": sha, "license": license_, "files": {}}
            for f in files:
                url = f"https://huggingface.co/datasets/{repo}/resolve/{sha}/{f}"
                dest = RAW / repo.replace("/", "__") / f
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.is_file():
                    r = http.get(url)
                    r.raise_for_status()
                    if r.url.host not in ALLOWED_HOSTS and not r.url.host.endswith(".hf.co"):
                        raise RuntimeError(f"unexpected redirect host {r.url.host}")
                    dest.write_bytes(r.content)
                digest = hashlib.sha256(dest.read_bytes()).hexdigest()
                entry["files"][f] = {"sha256": digest, "bytes": dest.stat().st_size}  # type: ignore[index]
                print(repo, f, dest.stat().st_size)
            manifest[repo] = entry
    (ROOT / "data" / "datasets_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
