provider "google" {
  project = var.project_id
  region  = var.region

  default_labels = {
    app = "llm-judge-calibration"
    env = "staging"
  }
}

module "judgecal" {
  source = "../../modules/judgecal"

  project_id               = var.project_id
  region                   = var.region
  env                      = "staging"
  deploy_services          = var.deploy_services
  image                    = var.image
  create_registry          = true
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
  subnet_cidr           = "10.40.0.0/24"
}
