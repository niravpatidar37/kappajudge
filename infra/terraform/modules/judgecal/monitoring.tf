# Monitoring as code: uptime check, alert policies, an availability SLO with multi-window burn-rate
# alerts, and app-specific alerts for the scheduled eval job (execution failures, failed-run events,
# release-gate drift). Created with the services (var.deploy_services); notifications go to
# var.alert_email when set (alerts still open incidents in the console without it).
#
# Latency note: the UI is Streamlit, whose browser sessions are long-lived websockets (up to the
# 3600s request timeout), so Cloud Run's request_latencies would be dominated by session length.
# The latency SLO is therefore measured on the JSON API (/api/v1/*) from the app's structured
# `api_request` log events (log-based distribution metric), not on the websocket traffic.

locals {
  monitoring_enabled = var.deploy_services
  channels           = [for c in google_monitoring_notification_channel.email : c.name]
  ui_service         = "${local.name}-ui"
  run_filter         = "resource.type = \"cloud_run_revision\" AND resource.labels.service_name = \"${local.ui_service}\""
  job_names          = ["${local.name}-eval", "${local.name}-migrate"]
}

resource "google_monitoring_notification_channel" "email" {
  count = var.alert_email == "" ? 0 : 1

  project      = var.project_id
  display_name = "kappajudge ${var.env} on-call (email)"
  type         = "email"
  labels = {
    email_address = var.alert_email
  }
  user_labels = local.labels

  depends_on = [google_project_service.apis]
}

# --------------------------------------------------------------------------- uptime
# /healthz through IAP: the check authenticates as the Monitoring service agent (OIDC), which is
# allowed through IAP below. Verify on the first apply that IAP accepts the uptime checker's token
# (see docs/RUNBOOK.md "Monitoring"); if it does not, keep the check and rely on the alerts below.
resource "google_monitoring_uptime_check_config" "healthz" {
  count = local.monitoring_enabled ? 1 : 0

  project      = var.project_id
  display_name = "${local.ui_service} /healthz"
  timeout      = "10s"
  period       = "60s"

  http_check {
    path         = "/healthz"
    port         = 443
    use_ssl      = true
    validate_ssl = true

    accepted_response_status_codes {
      status_value = 200
    }

    service_agent_authentication {
      type = "OIDC_TOKEN"
    }
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = trimprefix(google_cloud_run_v2_service.ui[0].uri, "https://")
    }
  }

  content_matchers {
    content = "\"ok\""
    matcher = "CONTAINS_STRING"
  }
}

resource "google_iap_web_cloud_run_service_iam_member" "uptime_checker" {
  count = local.monitoring_enabled ? 1 : 0

  project                = var.project_id
  location               = var.region
  cloud_run_service_name = google_cloud_run_v2_service.ui[0].name
  role                   = "roles/iap.httpsResourceAccessor"
  member                 = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-monitoring-notification.iam.gserviceaccount.com"
}

resource "google_monitoring_alert_policy" "uptime" {
  count = local.monitoring_enabled ? 1 : 0

  project               = var.project_id
  display_name          = "${local.ui_service}: /healthz uptime check failing"
  combiner              = "OR"
  severity              = "CRITICAL"
  notification_channels = local.channels
  user_labels           = local.labels

  conditions {
    display_name = "uptime check failed from >= 2 regions"

    condition_threshold {
      filter          = "metric.type = \"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.type = \"uptime_url\" AND metric.labels.check_id = \"${google_monitoring_uptime_check_config.healthz[0].uptime_check_id}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "300s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.host"]
      }
    }
  }

  documentation {
    subject   = "kappajudge ${var.env}: /healthz failing"
    mime_type = "text/markdown"
    content   = "The UI's liveness endpoint fails from several uptime regions. Runbook: docs/RUNBOOK.md#on-call-checklist"
  }
}

# --------------------------------------------------------------------------- 5xx ratio
resource "google_monitoring_alert_policy" "error_ratio" {
  count = local.monitoring_enabled ? 1 : 0

  project               = var.project_id
  display_name          = "${local.ui_service}: 5xx ratio > ${var.alert_5xx_ratio}"
  combiner              = "OR"
  severity              = "ERROR"
  notification_channels = local.channels
  user_labels           = local.labels

  conditions {
    display_name = "5xx / all requests over 5 min"

    condition_threshold {
      filter             = "metric.type = \"run.googleapis.com/request_count\" AND ${local.run_filter} AND metric.labels.response_code_class = \"5xx\""
      denominator_filter = "metric.type = \"run.googleapis.com/request_count\" AND ${local.run_filter}"
      comparison         = "COMPARISON_GT"
      threshold_value    = var.alert_5xx_ratio
      duration           = "300s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
      }

      denominator_aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }

  documentation {
    subject   = "kappajudge ${var.env}: 5xx ratio high"
    mime_type = "text/markdown"
    content   = "Server errors above ${var.alert_5xx_ratio * 100}% of requests. Check /readyz, Cloud SQL, the latest revision; roll back with update-traffic. docs/RUNBOOK.md#on-call-checklist"
  }
}

# --------------------------------------------------------------------------- API latency (p95)
resource "google_logging_metric" "api_latency" {
  count = local.monitoring_enabled ? 1 : 0

  project         = var.project_id
  name            = "${local.name}-api-latency"
  description     = "Latency of /api/v1/* requests from the app's structured api_request events (ms)."
  filter          = "${local.run_filter} AND jsonPayload.event = \"api_request\""
  value_extractor = "EXTRACT(jsonPayload.duration_ms)"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "ms"
  }

  bucket_options {
    exponential_buckets {
      num_finite_buckets = 32
      growth_factor      = 1.5
      scale              = 1
    }
  }
}

resource "google_monitoring_alert_policy" "api_latency_p95" {
  count = local.monitoring_enabled ? 1 : 0

  project               = var.project_id
  display_name          = "${local.ui_service}: API p95 latency > ${var.api_latency_slo_ms} ms"
  combiner              = "OR"
  severity              = "WARNING"
  notification_channels = local.channels
  user_labels           = local.labels

  conditions {
    display_name = "p95 of api_request duration over 10 min"

    condition_threshold {
      filter          = "metric.type = \"logging.googleapis.com/user/${google_logging_metric.api_latency[0].name}\" AND resource.type = \"cloud_run_revision\""
      comparison      = "COMPARISON_GT"
      threshold_value = var.api_latency_slo_ms
      duration        = "600s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_DELTA"
        cross_series_reducer = "REDUCE_PERCENTILE_95"
      }
    }
  }

  documentation {
    subject   = "kappajudge ${var.env}: API latency over SLO"
    mime_type = "text/markdown"
    content   = "p95 of /api/v1/* above ${var.api_latency_slo_ms} ms (docs/SLO.md). Usually Cloud SQL (CPU, connections) or cold starts."
  }
}

# --------------------------------------------------------------------------- saturation
resource "google_monitoring_alert_policy" "saturation" {
  count = local.monitoring_enabled ? 1 : 0

  project               = var.project_id
  display_name          = "${local.ui_service}: instance saturation"
  combiner              = "OR"
  severity              = "WARNING"
  notification_channels = local.channels
  user_labels           = local.labels

  conditions {
    display_name = "instances >= 80% of max_instances"

    condition_threshold {
      filter          = "metric.type = \"run.googleapis.com/container/instance_count\" AND ${local.run_filter}"
      comparison      = "COMPARISON_GT"
      threshold_value = floor(var.max_instances * 0.8)
      duration        = "600s"

      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_MAX"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }

  conditions {
    display_name = "container CPU p99 > 85%"

    condition_threshold {
      filter          = "metric.type = \"run.googleapis.com/container/cpu/utilizations\" AND ${local.run_filter}"
      comparison      = "COMPARISON_GT"
      threshold_value = 0.85
      duration        = "600s"

      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_PERCENTILE_99"
        cross_series_reducer = "REDUCE_MAX"
      }
    }
  }

  documentation {
    subject   = "kappajudge ${var.env}: UI saturated"
    mime_type = "text/markdown"
    content   = "Near max instances or CPU-bound. Raise max_instances/cpu in tfvars, or check for a hot loop in a Streamlit page."
  }
}

# --------------------------------------------------------------------------- SLO + burn rate
resource "google_monitoring_custom_service" "ui" {
  count = local.monitoring_enabled ? 1 : 0

  project      = var.project_id
  service_id   = "${local.name}-ui"
  display_name = "kappajudge ${var.env} UI"
  user_labels  = local.labels
}

resource "google_monitoring_slo" "availability" {
  count = local.monitoring_enabled ? 1 : 0

  project             = var.project_id
  service             = google_monitoring_custom_service.ui[0].service_id
  slo_id              = "availability"
  display_name        = "${var.slo_availability_goal * 100}% of requests are not 5xx (28-day rolling)"
  goal                = var.slo_availability_goal
  rolling_period_days = 28
  user_labels         = local.labels

  request_based_sli {
    good_total_ratio {
      bad_service_filter   = "metric.type = \"run.googleapis.com/request_count\" AND ${local.run_filter} AND metric.labels.response_code_class = \"5xx\""
      total_service_filter = "metric.type = \"run.googleapis.com/request_count\" AND ${local.run_filter}"
    }
  }
}

# Multi-window, multi-burn-rate (Google SRE workbook): page when the 1h AND 5m burn rates both exceed
# 14.4 (2% of a 30-day budget in 1h); ticket when the 6h AND 30m burn rates both exceed 6.
resource "google_monitoring_alert_policy" "burn_fast" {
  count = local.monitoring_enabled ? 1 : 0

  project               = var.project_id
  display_name          = "${local.ui_service}: error budget fast burn (1h & 5m > 14.4x)"
  combiner              = "AND"
  severity              = "CRITICAL"
  notification_channels = local.channels
  user_labels           = local.labels

  conditions {
    display_name = "burn rate 1h > 14.4"

    condition_threshold {
      filter          = "select_slo_burn_rate(\"${google_monitoring_slo.availability[0].name}\", \"3600s\")"
      comparison      = "COMPARISON_GT"
      threshold_value = 14.4
      duration        = "0s"
    }
  }

  conditions {
    display_name = "burn rate 5m > 14.4"

    condition_threshold {
      filter          = "select_slo_burn_rate(\"${google_monitoring_slo.availability[0].name}\", \"300s\")"
      comparison      = "COMPARISON_GT"
      threshold_value = 14.4
      duration        = "0s"
    }
  }

  documentation {
    subject   = "kappajudge ${var.env}: error budget burning fast"
    mime_type = "text/markdown"
    content   = "At this rate the 28-day availability budget is gone in about 2 days. Roll back first, investigate second. docs/RUNBOOK.md#rollback"
  }
}

resource "google_monitoring_alert_policy" "burn_slow" {
  count = local.monitoring_enabled ? 1 : 0

  project               = var.project_id
  display_name          = "${local.ui_service}: error budget slow burn (6h & 30m > 6x)"
  combiner              = "AND"
  severity              = "WARNING"
  notification_channels = local.channels
  user_labels           = local.labels

  conditions {
    display_name = "burn rate 6h > 6"

    condition_threshold {
      filter          = "select_slo_burn_rate(\"${google_monitoring_slo.availability[0].name}\", \"21600s\")"
      comparison      = "COMPARISON_GT"
      threshold_value = 6
      duration        = "0s"
    }
  }

  conditions {
    display_name = "burn rate 30m > 6"

    condition_threshold {
      filter          = "select_slo_burn_rate(\"${google_monitoring_slo.availability[0].name}\", \"1800s\")"
      comparison      = "COMPARISON_GT"
      threshold_value = 6
      duration        = "0s"
    }
  }

  documentation {
    subject   = "kappajudge ${var.env}: error budget burning"
    mime_type = "text/markdown"
    content   = "Sustained elevated 5xx. Open a ticket; check recent deploys and Cloud SQL health."
  }
}

# --------------------------------------------------------------------------- eval job (app-specific)
resource "google_monitoring_alert_policy" "job_failed" {
  count = local.monitoring_enabled ? 1 : 0

  project               = var.project_id
  display_name          = "${local.name}: eval or migrate job execution failed"
  combiner              = "OR"
  severity              = "ERROR"
  notification_channels = local.channels
  user_labels           = local.labels

  conditions {
    display_name = "completed executions with result=failed"

    condition_threshold {
      filter          = "metric.type = \"run.googleapis.com/job/completed_execution_count\" AND resource.type = \"cloud_run_job\" AND metric.labels.result = \"failed\" AND (resource.labels.job_name = \"${local.job_names[0]}\" OR resource.labels.job_name = \"${local.job_names[1]}\")"
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_DELTA"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["resource.label.job_name"]
      }
    }
  }

  documentation {
    subject   = "kappajudge ${var.env}: job execution failed"
    mime_type = "text/markdown"
    content   = "A scheduled eval or a migration failed. `gcloud run jobs executions list --job <job>`; logs: jsonPayload.event=\"eval_run_failed\" (error_type). Exit 75 = provider daily quota. docs/RUNBOOK.md#common-incidents"
  }
}

resource "google_monitoring_alert_policy" "eval_run_failed_event" {
  count = local.monitoring_enabled ? 1 : 0

  project               = var.project_id
  display_name          = "${local.name}-eval: eval run failed (app event, includes retried attempts)"
  combiner              = "OR"
  severity              = "WARNING"
  notification_channels = local.channels
  user_labels           = local.labels

  conditions {
    display_name = "jsonPayload.event = eval_run_failed"

    condition_matched_log {
      filter = "resource.type = \"cloud_run_job\" AND resource.labels.job_name = \"${local.job_names[0]}\" AND jsonPayload.event = \"eval_run_failed\""
      label_extractors = {
        error_type = "EXTRACT(jsonPayload.error_type)"
      }
    }
  }

  alert_strategy {
    auto_close = "86400s"

    notification_rate_limit {
      period = "3600s"
    }
  }

  documentation {
    subject   = "kappajudge ${var.env}: eval run failed"
    mime_type = "text/markdown"
    content   = "The eval job logged eval_run_failed (error_type label). QuotaExhausted: wait for the provider window; BudgetExceeded: spend cap; LLMDisabled: kill switch is on."
  }
}

resource "google_monitoring_alert_policy" "gate_drift" {
  count = local.monitoring_enabled ? 1 : 0

  project               = var.project_id
  display_name          = "${local.name}-eval: release-gate drift vs baseline"
  combiner              = "OR"
  severity              = "ERROR"
  notification_channels = local.channels
  user_labels           = local.labels

  conditions {
    display_name = "jsonPayload.event = eval_gate_drift"

    condition_matched_log {
      filter = "resource.type = \"cloud_run_job\" AND resource.labels.job_name = \"${local.job_names[0]}\" AND jsonPayload.event = \"eval_gate_drift\""
    }
  }

  alert_strategy {
    auto_close = "604800s"

    notification_rate_limit {
      period = "86400s"
    }
  }

  documentation {
    subject   = "kappajudge ${var.env}: scheduled eval drifted from the baseline"
    mime_type = "text/markdown"
    content   = "The scheduled eval's fresh scores fail the release gate against eval_runs/baseline.json (or the judge config no longer matches). Read jsonPayload.failures. Provider-side model drift is the usual cause: re-run, then re-baseline deliberately or pin a different model. docs/RUNBOOK.md#common-incidents"
  }
}
