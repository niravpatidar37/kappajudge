# One dedicated service account per workload, each with only what it needs:
#   ui:        the Streamlit labeling/trend app. Reads the app DB password; writes logs/metrics/traces.
#   jobs:      migration + scheduled eval jobs. Reads the owner DB password and the Groq key.
#   scheduler: may only start the eval job (roles/run.invoker on that one job).
# Cloud SQL is reached over private IP with a database password, so no roles/cloudsql.client.

resource "google_service_account" "ui" {
  project      = var.project_id
  account_id   = "${local.name}-ui"
  display_name = "llm-judge-calibration UI (${var.env})"

  depends_on = [google_project_service.apis]
}

resource "google_service_account" "jobs" {
  project      = var.project_id
  account_id   = "${local.name}-jobs"
  display_name = "llm-judge-calibration migrate + eval jobs (${var.env})"

  depends_on = [google_project_service.apis]
}

resource "google_service_account" "scheduler" {
  project      = var.project_id
  account_id   = "${local.name}-sched"
  display_name = "llm-judge-calibration eval scheduler (${var.env})"

  depends_on = [google_project_service.apis]
}

locals {
  telemetry_roles = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/cloudtrace.agent",
  ])
  runtime_members = {
    ui   = google_service_account.ui.member
    jobs = google_service_account.jobs.member
  }
  telemetry_bindings = merge([
    for sa, member in local.runtime_members : {
      for role in local.telemetry_roles : "${sa}:${role}" => { member = member, role = role }
    }
  ]...)
  deployer = var.deployer_service_account == "" ? "" : "serviceAccount:${var.deployer_service_account}"
}

resource "google_project_iam_member" "telemetry" {
  for_each = local.telemetry_bindings

  project = var.project_id
  role    = each.value.role
  member  = each.value.member
}

# --------------------------------------------------------------------------- GitHub Actions deployer (WIF)
# The deploy SA itself is created once during bootstrap (README "Deployment"); here it gets:
# deploy Cloud Run revisions/jobs, act as the runtime SAs, push images (registry.tf).
resource "google_project_iam_member" "deployer_run" {
  count = local.deployer == "" ? 0 : 1

  project = var.project_id
  role    = "roles/run.developer"
  member  = local.deployer
}

resource "google_service_account_iam_member" "deployer_act_as" {
  for_each = local.deployer == "" ? {} : {
    ui   = google_service_account.ui.name
    jobs = google_service_account.jobs.name
  }

  service_account_id = each.value
  role               = "roles/iam.serviceAccountUser"
  member             = local.deployer
}
