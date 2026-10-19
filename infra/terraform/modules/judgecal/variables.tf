variable "project_id" {
  description = "GCP project that hosts this environment."
  type        = string
}

variable "region" {
  description = "Region for Cloud Run, Cloud SQL, Artifact Registry and the VPC subnet."
  type        = string
  default     = "us-central1"
}

variable "env" {
  description = "Environment name (staging | prod). Used in every resource name."
  type        = string

  validation {
    condition     = contains(["staging", "prod"], var.env)
    error_message = "env must be staging or prod."
  }
}

variable "deploy_services" {
  description = "Create the Cloud Run service, jobs and scheduler. Leave false for the first apply (network, SQL, secrets, registry), add secret versions and push an image, then set true."
  type        = bool
  default     = false
}

variable "image" {
  description = "Container image by digest (REGION-docker.pkg.dev/PROJECT/judgecal/kappajudge@sha256:...). CD updates revisions after the first apply; Terraform ignores later image changes."
  type        = string
  default     = ""

  validation {
    condition     = var.image == "" || can(regex("@sha256:[0-9a-f]{64}$", var.image))
    error_message = "image must be pinned by digest (@sha256:...), not a tag."
  }
}

# ----------------------------------------------------------------------------- registry
variable "create_registry" {
  description = "Create the shared Artifact Registry repository in this environment (staging does; prod reuses it so the same digest is promoted)."
  type        = bool
  default     = false
}

variable "registry_repository_id" {
  description = "Artifact Registry repository id holding the image."
  type        = string
  default     = "judgecal"
}

# ----------------------------------------------------------------------------- Cloud Run sizing
variable "min_instances" {
  description = "Minimum warm instances (0 = scale to zero)."
  type        = number
  default     = 0
}

variable "max_instances" {
  description = "Maximum UI instances."
  type        = number
  default     = 3
}

variable "concurrency" {
  description = "Max concurrent requests (Streamlit sessions) per instance."
  type        = number
  default     = 8
}

variable "cpu" {
  description = "vCPU limit per UI instance."
  type        = string
  default     = "1"
}

variable "memory" {
  description = "Memory limit per UI instance (Streamlit + pandas)."
  type        = string
  default     = "2Gi"
}

variable "ingress" {
  description = "Cloud Run ingress. INGRESS_TRAFFIC_ALL is safe here because IAP (iap_enabled) authenticates every request; use INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER behind an external LB."
  type        = string
  default     = "INGRESS_TRAFFIC_ALL"

  validation {
    condition     = contains(["INGRESS_TRAFFIC_ALL", "INGRESS_TRAFFIC_INTERNAL_ONLY", "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"], var.ingress)
    error_message = "ingress must be a Cloud Run v2 ingress enum value."
  }
}

# ----------------------------------------------------------------------------- app config
variable "judge_model" {
  description = "Calibrated judge model used by the scheduled eval job."
  type        = string
  default     = "qwen/qwen3.8-27b"
}

variable "sut_model" {
  description = "System-under-test answer model used by the scheduled eval job."
  type        = string
  default     = "openai/gpt-oss-20b"
}

variable "eval_schedule" {
  description = "Cron schedule (UTC) for the eval job; empty disables the Cloud Scheduler trigger."
  type        = string
  default     = "0 6 * * 1"
}

variable "iap_members" {
  description = "Principals allowed through IAP to the labeling UI (user:..., group:..., serviceAccount:...). Never public."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for m in var.iap_members : can(regex("^(user|group|serviceAccount|domain):", m))])
    error_message = "iap_members must be user:, group:, serviceAccount: or domain: principals; allUsers/allAuthenticatedUsers are refused (labeling data is private)."
  }
}

variable "operator_invokers" {
  description = "Principals granted roles/run.invoker so operators can reach the UI with `gcloud run services proxy` (IAM-authenticated tunnel). Never public."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for m in var.operator_invokers : can(regex("^(user|group|serviceAccount):", m))])
    error_message = "operator_invokers must be user:, group: or serviceAccount: principals; allUsers/allAuthenticatedUsers are refused."
  }
}

variable "kill_switch" {
  description = "true sets LLM_CALLS_ENABLED=false on the eval job (no model calls). Emergency use: prefer `gcloud run jobs update --update-env-vars`, then reconcile here."
  type        = bool
  default     = false
}

variable "llm_spend_cap_usd" {
  description = "LLM_SPEND_CAP_USD for each job execution's local ledger."
  type        = number
  default     = 1
}

# ----------------------------------------------------------------------------- Cloud SQL
variable "sql_tier" {
  description = "Cloud SQL machine tier (Enterprise edition)."
  type        = string
  default     = "db-custom-1-3840"
}

variable "sql_high_availability" {
  description = "REGIONAL (HA, synchronous standby) instead of ZONAL."
  type        = bool
  default     = false
}

variable "sql_disk_size_gb" {
  description = "Initial SSD size; autoresize is on."
  type        = number
  default     = 10
}

variable "deletion_protection" {
  description = "Protect Cloud SQL and Cloud Run resources from terraform destroy."
  type        = bool
  default     = true
}

variable "db_password_version" {
  description = "Bump to rotate both database passwords (new random values are written to Cloud SQL and Secret Manager; never stored in state)."
  type        = number
  default     = 1
}

# ----------------------------------------------------------------------------- network
variable "subnet_cidr" {
  description = "Subnet used by Cloud Run Direct VPC egress."
  type        = string
  default     = "10.10.0.0/24"
}

variable "psa_prefix_length" {
  description = "Prefix length of the Private Service Access range reserved for Cloud SQL."
  type        = number
  default     = 20
}

# ----------------------------------------------------------------------------- CI/CD identity
variable "deployer_service_account" {
  description = "Email of the GitHub Actions deploy service account (Workload Identity Federation). Empty = no deploy IAM bindings."
  type        = string
  default     = ""
}

variable "labels" {
  description = "Extra labels on every labelled resource."
  type        = map(string)
  default     = {}
}

# ----------------------------------------------------------------------------- monitoring
variable "alert_email" {
  description = "Email for the on-call notification channel. Empty = no channel (alerts still open incidents in the console)."
  type        = string
  default     = ""
}

variable "alert_5xx_ratio" {
  description = "Alert when 5xx responses exceed this fraction of requests over 5 minutes."
  type        = number
  default     = 0.05
}

variable "api_latency_slo_ms" {
  description = "p95 latency objective for /api/v1/* in milliseconds (docs/SLO.md)."
  type        = number
  default     = 1000
}

variable "slo_availability_goal" {
  description = "Availability SLO (fraction of non-5xx requests over 28 days)."
  type        = number
  default     = 0.995

  validation {
    condition     = var.slo_availability_goal > 0.9 && var.slo_availability_goal < 1
    error_message = "slo_availability_goal must be between 0.9 and 1 (exclusive)."
  }
}
