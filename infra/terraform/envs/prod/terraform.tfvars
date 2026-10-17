# Prod: one warm instance, regional HA Cloud SQL, deletion protection on, daily eval.
# project_id, image, deployer_service_account and iap_members are passed at plan time.
min_instances         = 1
max_instances         = 3
concurrency           = 20
cpu                   = "1"
memory                = "1Gi"
sql_tier              = "db-custom-1-3840"
sql_high_availability = true
deletion_protection   = true
llm_spend_cap_usd     = 1
eval_schedule         = "0 6 * * *"
