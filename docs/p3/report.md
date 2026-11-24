# Harness report - cristancho-urrego

Automated score: **70.0 / 70.0**

| Req | Title | Status | Points |
|---|---|---|---|
| R1 | Every API action emits one valid event | passed | 7.0/7 |
| R2 | No event loss under burst, API stays fast | passed | 6.0/6 |
| R3 | Live view fed by the stream (event time) | passed | 7.0/7 |
| R4 | Events reach the S3 landing zone | passed | 4.0/4 |
| R5 | Star schema tables per contract | passed | 7.0/7 |
| R6 | Star integrity (keys, orphans, duplicates) | passed | 5.0/5 |
| R7 | Business queries return the right numbers | passed | 11.0/11 |
| R8 | Dirty events handled, refresh in time | passed | 6.0/6 |
| R9 | Slowly changing dimension (type 2) | passed | 8.0/8 |
| R10 | Partition pruning and scan limit | passed | 4.0/4 |
| R11 | Minimal Kinesis stream | passed | 3.0/3 |
| R12 | Cost projection | passed | 2.0/2 |

## R1 Every API action emits one valid event - passed
- passed: `test_each_action_emits_one_valid_event` 
- evidence: 30 search events for 30 calls; 4 reservation events for 3 reservations + 1 cancel

## R2 No event loss under burst, API stays fast - passed
- passed: `test_burst_no_loss_and_fast_api` 
- evidence: 200 searches, concurrency 50: p95=0.237s, status codes [200]
- evidence: 200 events in the stream for 200 requests

## R3 Live view fed by the stream (event time) - passed
- passed: `test_live_reflects_new_events` 
- passed: `test_live_uses_event_time` 
- evidence: live deltas after 1.0s: searches 10/10, reservations 3/3, cancellations 1/1
- evidence: live searches after replaying 17000 old events: 240

## R4 Events reach the S3 landing zone - passed
- passed: `test_events_reach_landing_zone` 
- evidence: 19912/19912 distinct replay events found in S3 after 32s

## R5 Star schema tables per contract - passed
- passed: `test_tables_have_required_columns` 
- passed: `test_tables_are_parquet` 
- passed: `test_fact_search_partitioned_by_month` 
- passed: `test_dim_date_is_enriched_correctly` 

## R6 Star integrity (keys, orphans, duplicates) - passed
- passed: `test_dimension_keys_unique` 
- passed: `test_no_orphan_foreign_keys` 
- passed: `test_fact_grain_one_row_per_event` 

## R7 Business queries return the right numbers - passed
- passed: `test_q1_revenue_by_month_and_fare_class` 
- passed: `test_q2_cancellations_per_route` 
- passed: `test_q3_bookings_per_weekday_name` 
- passed: `test_q7_holidays_vs_other_days` 
- passed: `test_q8_long_weekends` 
- passed: `test_q4_top10_routes_by_revenue` 
- passed: `test_q6_conversion_inputs_per_route` 
- evidence: conversion (bookings / searches) per route matches the oracle

## R8 Dirty events handled, refresh in time - passed
- passed: `test_refresh_finishes_in_time` 
- passed: `test_duplicates_removed` 
- passed: `test_late_events_use_event_date` 
- evidence: refresh state SUCCEEDED in 31s

## R9 Slowly changing dimension (type 2) - passed
- passed: `test_route_dimension_is_type2` 
- passed: `test_q5_revenue_uses_category_valid_at_event_date` 

## R10 Partition pruning and scan limit - passed
- passed: `test_one_month_query_scans_less` 
- passed: `test_workgroup_has_scan_limit` 
- evidence: one month scanned 3845 B vs 11209 B for all (0.34)

## R11 Minimal Kinesis stream - passed
- passed: `test_stream_is_minimal` 
- evidence: stream: {'mode': 'PROVISIONED', 'open_shards': 1, 'retention_hours': 24, 'consumers': 0}

## R12 Cost projection - passed
- passed: `test_projection_within_budget` 
- passed: `test_projection_lists_the_services_used` 
- evidence: declared 31.62 USD/month; limit 50.00
