provider "google" {
  project = var.project_id
  region  = var.region

  default_labels = {
    app = "llm-judge-calibration"
    env = "prod"
  }
}

module "judgecal" {
  source = "../../modules/judgecal"

  project_id               = var.project_id
  region                   = var.region
  env                      = "prod"
  deploy_services          = var.deploy_services
  image                    = var.image
  create_registry          = false
  deployer_service_account = var.deployer_service_account
  iap_members              = var.iap_members

  min_instances         = var.min_instances
  max_instances         = var.max_instances
  concurrency           = var.concurrency
  cpu                   = var.cpu
  memory                = var.memory
  sql_tier              = var.sql_tier
  sql_high_availability = var.sql_high_availability
  deletion_protection   = var.deletion_protection
  llm_spend_cap_usd     = var.llm_spend_cap_usd
  eval_schedule         = var.eval_schedule
  kill_switch           = var.kill_switch
  subnet_cidr           = "10.41.0.0/24"
}
