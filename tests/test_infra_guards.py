"""Static guards on the deploy configuration: the labeling UI is never public, and the smoke test,
kill switch and recorded-mode refusal are wired into the cloud config. Each assertion fails if the
corresponding line is removed from infra/ or .github/."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TF = ROOT / "infra" / "terraform"


def _tf() -> dict[str, str]:
    files = [p for p in TF.rglob("*.tf") if ".terraform" not in p.parts]
    return {str(p.relative_to(TF)): p.read_text(encoding="utf-8") for p in files}


def _resource(text: str, rtype: str, name: str) -> str:
    m = re.search(rf'resource "{rtype}" "{name}" \{{(.*?)\n\}}', text, re.S)
    assert m, f"{rtype}.{name} missing"
    return m.group(1)


def test_no_public_principal_anywhere_in_terraform() -> None:
    for path, text in _tf().items():
        code = "\n".join(line for line in text.splitlines() if "error_message" not in line)
        assert not re.search(r'"all(Authenticated)?Users"', code), f"public principal in {path}"


def test_ui_service_is_behind_iap_and_only_iap_agent_or_named_operators_invoke() -> None:
    run = _tf()[str(Path("modules/judgecal/run.tf"))]
    ui = _resource(run, "google_cloud_run_v2_service", "ui")
    assert re.search(r"iap_enabled\s*=\s*true", ui)
    invokers = re.findall(r'resource "google_cloud_run_v2_service_iam_member" "(\w+)" \{(.*?)\n\}', run, re.S)
    assert {n for n, _ in invokers} == {"iap_invoker", "operator_invokers"}
    bodies = dict(invokers)
    assert "gcp-sa-iap.iam.gserviceaccount.com" in bodies["iap_invoker"]
    assert "var.operator_invokers" in bodies["operator_invokers"]
    variables = _tf()[str(Path("modules/judgecal/variables.tf"))]
    for var in ("iap_members", "operator_invokers"):
        block = re.search(rf'variable "{var}" \{{(.*?)\n\}}', variables, re.S)
        assert block and "validation" in block.group(1) and "^(user|group|serviceAccount" in block.group(1)


def test_ui_env_kill_switch_env_and_api_key_secret() -> None:
    run = _tf()[str(Path("modules/judgecal/run.tf"))]
    assert re.search(r'ui_env = merge\(local.common_env, \{[^}]*LLM_CALLS_ENABLED\s*=\s*"false"', run, re.S)
    assert re.search(r"ENV\s*=\s*var.env", run)
    assert not re.search(r"LLM_RECORDED_MODE\s*=", run)  # never configured in the cloud
    assert re.search(r'API_KEYS\s*=\s*"ui-api-keys"', run)


def test_alerts_cover_eval_failures_and_gate_drift() -> None:
    mon = _tf()[str(Path("modules/judgecal/monitoring.tf"))]
    assert 'jsonPayload.event = \\"eval_gate_drift\\"' in mon
    assert 'jsonPayload.event = \\"eval_run_failed\\"' in mon
    assert "run.googleapis.com/job/completed_execution_count" in mon
    assert "select_slo_burn_rate" in mon and "uptime_check_config" in mon


def test_deploy_runs_the_smoke_script_and_job_preflight_with_rollback() -> None:
    wf = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    # staging canary + main; prod canary at 0%, canary URL + split during the soak, 100%
    assert wf.count("bash scripts/deploy_smoke.sh") >= 6
    assert "python3 scripts/smoke.py" in (ROOT / "scripts" / "deploy_smoke.sh").read_text(encoding="utf-8")
    assert "judgecal.evalrun,--smoke" in wf
    assert wf.count("update-traffic") >= 4 and "rollback" in wf.lower()
    assert "cosign verify" in wf
