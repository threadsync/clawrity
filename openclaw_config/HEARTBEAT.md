# HEARTBEAT -- Clawrity Daily Digest

## Schedule

- trigger: daily
- time: "08:00"
- timezone: "Asia/Kolkata"

## Tasks

Execute these tasks in order every day at 08:00 IST:

1. **Data Digest** -- Make HTTP POST to `http://localhost:8000/digest` with payload `{"client_id": "acme_corp"}`. This returns the full business intelligence digest covering bottom-performing branches, revenue trends, and budget recommendations.

2. **Post to Slack** -- Post the result from step 1 to the `#all-global-super-store` Slack channel. Format:
   ```
   Clawrity Daily Digest -- ACME Corporation
   [Date]

   [Data Digest from Step 1]
   ```

## Retry

- on_failure: retry after 15 minutes
- max_retries: 3
- If all retries fail, post this message to `#all-global-super-store`:
  > Clawrity digest unavailable. Backend may be offline.

## Notes

- The digest endpoint runs the full pipeline: SQL query --> Gen Agent --> QA Agent --> formatted output
