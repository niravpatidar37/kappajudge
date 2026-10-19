variable "project_id" {
  description = "GCP project id (pass with -var or TF_VAR_project_id; not committed)."
  type        = string
}

variable "region" {
  description = "GCP region."
  type        = string
  default     = "us-central1"
}

variable "image" {
  description = "Image by digest for the first deploy_services apply; CD owns it afterwards."
  type        = string
  default     = ""
}

variable "deploy_services" {
  description = "Second-phase switch: create the Cloud Run service, jobs and scheduler."
  type        = bool
  default     = false
}

variable "deployer_service_account" {
  description = "GitHub Actions deploy service account email (Workload Identity Federation)."
  type        = string
  default     = ""
}

variable "iap_members" {
  description = "Principals allowed through IAP to the labeling UI (user:..., group:...)."
  type        = list(string)
  default     = []
}

variable "min_instances" {
  description = "Minimum Cloud Run instances."
  type        = number
}

variable "max_instances" {
  description = "Maximum Cloud Run instances."
  type        = number
}

variable "concurrency" {
  description = "Concurrent requests per instance."
  type        = number
}

variable "cpu" {
  description = "vCPU per instance."
  type        = string
}

variable "memory" {
  description = "Memory per instance."
  type        = string
}

variable "sql_tier" {
  description = "Cloud SQL tier."
  type        = string
}

variable "sql_high_availability" {
  description = "Regional (HA) Cloud SQL."
  type        = bool
}

variable "deletion_protection" {
  description = "Protect stateful resources from destroy."
  type        = bool
}

variable "llm_spend_cap_usd" {
  description = "Per-execution LLM spend cap (local ledger) for the eval job."
  type        = number
}

variable "eval_schedule" {
  description = "Cron (UTC) for the scheduled eval job; empty disables it."
  type        = string
}

variable "kill_switch" {
  description = "Disable all LLM calls in the eval job."
  type        = bool
  default     = false
}

variable "operator_invokers" {
  description = "Principals allowed to use `gcloud run services proxy` (roles/run.invoker). Never public."
  type        = list(string)
  default     = []
}

variable "alert_email" {
  description = "On-call email for alert notifications (pass at plan time; not committed)."
  type        = string
  default     = ""
}
