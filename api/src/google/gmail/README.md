# Gmail routes and public email studio

`GET /api/google/gmail/get_zillow_emails` retains its old URL but now returns a
catalog of three wholly fictional scenarios and default instructions from
`demo.py`. It never queries Gmail or the database. The public page renders plain
text, with no email HTML or third-party image loads.

`POST /api/google/gmail/generate_email_response` accepts only `scenario_id` and
`system_instruction` (1–2,000 characters). The server selects the source email
and facts. It returns a plain-text draft; it cannot send email or call tools.
System safety rules are separate from visitor preferences and source email text.
The model has an 800-token output ceiling and low reasoning effort.

Inference uses `api.src.utils.llm` and the dedicated server-side
`PUBLIC_PORTFOLIO_OPENROUTER_API_KEY`. It never falls back to operational SerniaAI keys.
Obtain a key from [OpenRouter](https://openrouter.ai/settings/keys); see
[API documentation](https://openrouter.ai/docs/api-reference/overview). Never expose
it to browser code. Budget exhaustion returns a safe 402 and pauses drafting in the page; rate limits return 429 with a one-minute cooldown. Existing drafts remain visible. Other provider errors return a generic 503; inspect server Logfire
telemetry for diagnostics. The shared client bounds timeouts and retries, and the
public demo middleware applies usage controls.

The `/watch/*` routes remain authenticated operational Gmail watch controls.
They are separate from the fictional studio.
