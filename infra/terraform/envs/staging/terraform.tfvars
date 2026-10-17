# Staging: scale to zero, small zonal Cloud SQL, weekly eval. project_id, image,
# deployer_service_account and iap_members are passed at plan time (-var / TF_VAR_*).
min_instances         = 0
max_instances         = 2
concurrency           = 20
cpu                   = "1"
memory                = "1Gi"
sql_tier              = "db-custom-1-3840"
sql_high_availability = false
deletion_protection   = false
llm_spend_cap_usd     = 0.5
eval_schedule         = "0 6 * * 1"
