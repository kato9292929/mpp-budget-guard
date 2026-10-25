"""Install this skill into $HERMES_HOME from a LOCAL directory using Hermes's own
hub pipeline (bundle -> quarantine -> skills_guard scan -> install_from_quarantine),
i.e. the same steps `hermes skills install` runs after fetching from GitHub, and
print the scan verdict/findings plus the advisory Tier-1 report.

Hermes has no local-path source for `hermes skills install`, hence this script.
Usage (Hermes venv python, cwd = Hermes source tree, HERMES_HOME = a fresh dir):
    python local_install.py <skill-dir> [category]
"""
import json
import sys
from pathlib import Path

from tools.skills_guard import format_scan_report, scan_skill, should_allow_install
from tools.skills_hub_install import install_from_quarantine, quarantine_bundle
from tools.skills_hub_models import SkillBundle

src = Path(sys.argv[1]).resolve()
category = sys.argv[2] if len(sys.argv) > 2 else "payments"
SKIP_DIRS = {"node_modules", "__pycache__", "var", ".git"}

files = {}
for p in sorted(src.rglob("*")):
    rel = p.relative_to(src)
    if not p.is_file() or any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts):
        continue
    data = p.read_bytes()
    try:
        files[rel.as_posix()] = data.decode("utf-8")
    except UnicodeDecodeError:
        files[rel.as_posix()] = data

bundle = SkillBundle(name="mpp-budget-guard", files=files, source="github",
                     identifier="local:" + str(src), trust_level="community")
q = quarantine_bundle(bundle)
result = scan_skill(q, source="community")
allowed, reason = should_allow_install(result)
print(format_scan_report(result))
try:
    from tools.skillevaluator_scan import format_tier1_report, run_tier1_scan
    report = run_tier1_scan(q)
    print("--- tier1 advisory ---")
    print(format_tier1_report(report) if report.available else "tier1 scanner not available")
except Exception as exc:  # noqa: BLE001 - advisory only
    print(f"--- tier1 advisory unavailable: {exc}")
summary = {"files": len(files), "verdict": result.verdict, "findings": len(result.findings),
           "allowed": allowed, "reason": reason}
if allowed:
    summary["installed_to"] = str(install_from_quarantine(q, bundle.name, category, bundle, result))
print(json.dumps(summary, indent=2))
