# Cloud Run: the Streamlit UI service (behind IAP) plus two jobs from the same image:
#   migrate: applies numbered SQL migrations (run by CD before each rollout).
#   eval:    scheduled release-gate eval (system-under-test prompt + calibrated judge); stores the run
#            in Postgres for the trend page. Triggered by Cloud Scheduler.
# Created only when var.deploy_services is true (second apply, after secrets have versions and an
# image exists). After creation, the CD workflow owns the image and traffic split; Terraform
# ignores those fields so a plan never rolls a deploy back.

locals {
  vpc_egress = {
    subnetwork = google_compute_subnetwork.run.id
  }

  common_env = {
    LOG_LEVEL          = "INFO"
    LLM_SPEND_CAP_USD  = tostring(var.llm_spend_cap_usd)
    LLM_SPEND_LEDGER   = "/tmp/judgecal-spend-ledger.jsonl"
    JUDGECAL_CACHE_DIR = "/tmp/judgecal-cache"
    PG_HOST            = google_sql_database_instance.pg.private_ip_address
    PG_PORT            = "5432"
    PG_DB              = google_sql_database.judgecal.name
    PG_SSLMODE         = "require"
    PORTFOLIO_ENV_FILE = "/nonexistent"
  }

  ui_env = merge(local.common_env, {
    PG_USER           = google_sql_user.app.name
    LLM_CALLS_ENABLED = "false" # the UI never calls a model
  })

  job_env = merge(local.common_env, {
    PG_USER           = google_sql_user.owner.name
    LLM_CALLS_ENABLED = var.kill_switch ? "false" : "true"
    JUDGE_MODEL       = var.judge_model
    SUT_MODEL         = var.sut_model
  })

  ui_secret_env = {
    POSTGRES_PASSWORD = "pg-app-password"
  }

  job_secret_env = {
    GROQ_API_KEY      = "groq-api-key"
    POSTGRES_PASSWORD = "pg-owner-password"
  }
}

resource "google_cloud_run_v2_service" "ui" {
  count = var.deploy_services ? 1 : 0

  project             = var.project_id
  name                = "${local.name}-ui"
  location            = var.region
  ingress             = var.ingress
  deletion_protection = var.deletion_protection
  iap_enabled         = true # Google identity + IAP access check before any request reaches Streamlit
  labels              = local.labels

  template {
    service_account                  = google_service_account.ui.email
    timeout                          = "3600s" # Streamlit keeps a websocket open per session
    max_instance_request_concurrency = var.concurrency
    session_affinity                 = true # Streamlit sessions are in-process state
    execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"
    labels                           = local.labels

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    vpc_access {
      egress = "PRIVATE_RANGES_ONLY" # Cloud SQL via the VPC

      network_interfaces {
        subnetwork = local.vpc_egress.subnetwork
      }
    }

    containers {
      name  = "ui"
      image = var.image

      ports {
        container_port = 8504
      }

      resources {
        limits = {
          cpu    = var.cpu
          memory = var.memory
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      dynamic "env" {
        for_each = local.ui_env
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = local.ui_secret_env
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.s[env.value].secret_id
              version = "latest" # resolved at instance start; rotate = add version + new revision
            }
          }
        }
      }

      # Startup: traffic only once Postgres answers and migrations are applied (/readyz).
      startup_probe {
        initial_delay_seconds = 0
        period_seconds        = 5
        timeout_seconds       = 4
        failure_threshold     = 24

        http_get {
          path = "/readyz"
          port = 8504
        }
      }

      # Liveness: process health only, so a database outage does not restart-loop instances.
      liveness_probe {
        period_seconds    = 15
        timeout_seconds   = 3
        failure_threshold = 3

        http_get {
          path = "/healthz"
          port = 8504
        }
      }
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
      template[0].revision,
      traffic,
      client,
      client_version,
    ]
  }

  depends_on = [
    google_secret_manager_secret_iam_member.readers,
    google_secret_manager_secret_version.pg_app,
    google_project_iam_member.telemetry,
  ]
}

# IAP calls Cloud Run as its service agent; nobody else gets run.invoker (no allUsers).
resource "google_cloud_run_v2_service_iam_member" "iap_invoker" {
  count = var.deploy_services ? 1 : 0

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.ui[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-iap.iam.gserviceaccount.com"
}

# Who may open the labeling UI through IAP (e.g. group:labelers@example.com, user:owner@example.com).
resource "google_iap_web_cloud_run_service_iam_member" "labelers" {
  for_each = var.deploy_services ? toset(var.iap_members) : toset([])

  project                = var.project_id
  location               = var.region
  cloud_run_service_name = google_cloud_run_v2_service.ui[0].name
  role                   = "roles/iap.httpsResourceAccessor"
  member                 = each.value
}

# --------------------------------------------------------------------------- jobs
resource "google_cloud_run_v2_job" "migrate" {
  count = var.deploy_services ? 1 : 0

  project             = var.project_id
  name                = "${local.name}-migrate"
  location            = var.region
  deletion_protection = var.deletion_protection
  labels              = local.labels

  template {
    task_count  = 1
    parallelism = 1

    template {
      service_account       = google_service_account.jobs.email
      timeout               = "600s"
      max_retries           = 0 # a failed migration must stop the deploy, not retry half-applied work
      execution_environment = "EXECUTION_ENVIRONMENT_GEN2"

      vpc_access {
        egress = "PRIVATE_RANGES_ONLY"

        network_interfaces {
          subnetwork = local.vpc_egress.subnetwork
        }
      }

      containers {
        name    = "migrate"
        image   = var.image
        command = ["python"]
        args    = ["-m", "judgecal.cli", "migrate"]

        resources {
          limits = {
            cpu    = "1"
            memory = "1Gi"
          }
        }

        dynamic "env" {
          for_each = local.job_env
          content {
            name  = env.key
            value = env.value
          }
        }

        dynamic "env" {
          for_each = local.job_secret_env
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = google_secret_manager_secret.s[env.value].secret_id
                version = "latest"
              }
            }
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].template[0].containers[0].image,
      client,
      client_version,
    ]
  }

  depends_on = [
    google_secret_manager_secret_iam_member.readers,
    google_secret_manager_secret_version.pg_owner,
    google_project_iam_member.telemetry,
  ]
}

resource "google_cloud_run_v2_job" "eval" {
  count = var.deploy_services ? 1 : 0

  project             = var.project_id
  name                = "${local.name}-eval"
  location            = var.region
  deletion_protection = var.deletion_protection
  labels              = local.labels

  template {
    task_count  = 1
    parallelism = 1

    template {
      service_account       = google_service_account.jobs.email
      timeout               = "3600s" # free-tier rate limits make a 15-item run take minutes
      max_retries           = 1       # cached by content hash, so a retry only pays for unfinished items
      execution_environment = "EXECUTION_ENVIRONMENT_GEN2"

      vpc_access {
        egress = "PRIVATE_RANGES_ONLY" # Cloud SQL via the VPC; Groq over the internet

        network_interfaces {
          subnetwork = local.vpc_egress.subnetwork
        }
      }

      containers {
        name    = "eval"
        image   = var.image
        command = ["python"]
        args    = ["-m", "judgecal.evalrun", "--store", "--out", "/tmp/eval_runs"]

        resources {
          limits = {
            cpu    = "1"
            memory = "1Gi"
          }
        }

        dynamic "env" {
          for_each = local.job_env
          content {
            name  = env.key
            value = env.value
          }
        }

        dynamic "env" {
          for_each = local.job_secret_env
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = google_secret_manager_secret.s[env.value].secret_id
                version = "latest"
              }
            }
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].template[0].containers[0].image,
      client,
      client_version,
    ]
  }

  depends_on = [
    google_secret_manager_secret_iam_member.readers,
    google_secret_manager_secret_version.pg_owner,
    google_project_iam_member.telemetry,
  ]
}

resource "google_cloud_run_v2_job_iam_member" "scheduler_invoke_eval" {
  count = var.deploy_services ? 1 : 0

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.eval[0].name
  role     = "roles/run.invoker"
  member   = google_service_account.scheduler.member
}

resource "google_cloud_scheduler_job" "eval" {
  count = var.deploy_services && var.eval_schedule != "" ? 1 : 0

  project          = var.project_id
  region           = var.region
  name             = "${local.name}-eval-schedule"
  description      = "Runs the release-gate eval job; results land in Postgres (trend page)."
  schedule         = var.eval_schedule
  time_zone        = "Etc/UTC"
  attempt_deadline = "60s"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.eval[0].name}:run"

    oauth_token {
      service_account_email = google_service_account.scheduler.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [google_cloud_run_v2_job_iam_member.scheduler_invoke_eval]
}
