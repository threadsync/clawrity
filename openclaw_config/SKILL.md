# SKILL — Clawrity Business Intelligence Query

## When to Invoke

Invoke this skill when the user asks ANY question about:
- Revenue, sales, profit, or financial performance
- Marketing spend, budget allocation, or ROI
- Branch or city performance comparisons
- Lead generation, conversions, or pipeline metrics
- Forecasts, projections, or trend analysis
- Budget recommendations or reallocation suggestions
- Channel performance (social media, email, PPC, etc.)
- Country or region performance

## Endpoint

**URL:** `http://localhost:8000/chat`
**Method:** POST
**Content-Type:** application/json

## Payload

```json
{
  "client_id": "acme_corp",
  "message": "<user's exact question>"
}
```

## Response Handling

- The API returns a JSON object with a `response` field containing the formatted answer
- Return the `response` field **exactly as received** — do not summarize or modify
- The response is already formatted in markdown with data tables, bullet points, and recommendations

## Error Handling

- **HTTP 500 or timeout:** Return "Data query failed. Try again in a moment."
- **HTTP 404:** Return "Client configuration not found. Please contact support."
- **Connection refused:** Return "Clawrity backend is currently offline. Please ensure the service is running."

## Examples

User: "What are the bottom 3 branches by revenue this week?"
→ POST to `/chat` with `{"client_id": "acme_corp", "message": "What are the bottom 3 branches by revenue this week?"}`

User: "Should I increase Toronto's budget?"
→ POST to `/chat` with `{"client_id": "acme_corp", "message": "Should I increase Toronto's budget?"}`

User: "Compare social media vs email spend efficiency"
→ POST to `/chat` with `{"client_id": "acme_corp", "message": "Compare social media vs email spend efficiency"}`
