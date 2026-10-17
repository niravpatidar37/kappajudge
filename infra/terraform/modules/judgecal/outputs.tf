output "service_name" {
  description = "Cloud Run UI service name (empty until deploy_services = true)."
  value       = var.deploy_services ? google_cloud_run_v2_service.ui[0].name : ""
}

output "service_url" {
  description = "Cloud Run UI URL (IAP-protected)."
  value       = var.deploy_services ? google_cloud_run_v2_service.ui[0].uri : ""
}

output "migrate_job" {
  description = "Cloud Run Job that applies schema migrations."
  value       = var.deploy_services ? google_cloud_run_v2_job.migrate[0].name : ""
}

output "eval_job" {
  description = "Cloud Run Job that runs the scheduled release-gate eval."
  value       = var.deploy_services ? google_cloud_run_v2_job.eval[0].name : ""
}

output "registry_url" {
  description = "Docker registry path for images."
  value       = local.registry_url
}

output "sql_instance" {
  description = "Cloud SQL instance name."
  value       = google_sql_database_instance.pg.name
}

output "ui_service_account" {
  description = "Runtime service account of the UI."
  value       = google_service_account.ui.email
}

output "jobs_service_account" {
  description = "Runtime service account of the jobs."
  value       = google_service_account.jobs.email
}

output "secret_ids" {
  description = "Secret Manager secret ids (values are added out of band)."
  value       = { for k, s in google_secret_manager_secret.s : k => s.secret_id }
}
