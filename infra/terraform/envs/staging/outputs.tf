output "service_url" {
  description = "Cloud Run UI URL (IAP-protected)."
  value       = module.judgecal.service_url
}

output "service_name" {
  description = "Cloud Run UI service name."
  value       = module.judgecal.service_name
}

output "migrate_job" {
  description = "Migration job name."
  value       = module.judgecal.migrate_job
}

output "eval_job" {
  description = "Scheduled eval job name."
  value       = module.judgecal.eval_job
}

output "registry_url" {
  description = "Artifact Registry path."
  value       = module.judgecal.registry_url
}

output "sql_instance" {
  description = "Cloud SQL instance."
  value       = module.judgecal.sql_instance
}

output "secret_ids" {
  description = "Secret Manager secret ids to populate."
  value       = module.judgecal.secret_ids
}
