# HEARTBEAT — ACME Corporation

## Schedule
- trigger: daily
- time: "08:00"
- timezone: "Asia/Kolkata"

## Digest Tasks
1. Pull last 7 days spend + revenue per branch
2. Identify bottom 3 performing branches by revenue
3. Generate newsletter-style summary via Gen Agent → QA Agent
4. Run Scout Agent for competitor + sector news
5. Append Market Intelligence section to digest
6. Push complete digest to Slack channel

## Retry
- on_failure: retry after 15 minutes
- max_retries: 3
