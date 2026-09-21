# GoHighLevel Expert – lead automation, AI qualification & workflows

A working example of the automation we build for GoHighLevel users: **every new
lead scored the moment it arrives, hot leads moved to the right pipeline stage,
and follow-up tasks created — automatically.** No lead waits for a human to
notice it.

Built by [Vroom Analytics](https://vroomanalytics.com) as public proof for our
**AI automation service** — *"Stop losing business to the missed call."*

> **Companion article:** _coming soon — will be linked here_
> (nothing on the staging blog covers this yet — see the audit note below)

---

## What this is

When a new contact, form submission, or appointment hits your GoHighLevel
account, GHL fires a webhook. This code catches it, scores the lead
**hot / warm / cold** using transparent rules (every point explained in plain
language — no black box), then writes the result back into GHL: hot leads move
to your "Qualified" stage and get an urgent follow-up task, warm leads get a
task, cold leads go to nurture.

Two hard rules, enforced in code:

1. **Never guess.** A lead with no email and no phone gets *"insufficient
   data"* — not a fabricated score — plus a manual-review task for a human.
2. **Never silently drop.** If the CRM is down, writes wait in a retry queue
   with exponential backoff. Poison payloads (bad JSON, unknown events) are
   rejected outright — never retried forever.

## How it works

```
  GHL webhook            webhook.py          qualifier.py           crm_client.py
 (contact/form/      ->  parse + validate ->  score hot/warm/  ->  move stage +
  appointment event)     + normalize        cold (rule-based,      create task
                                              explainable)         via adapter
                                                     |
                                                     v
                                              pipeline.py orchestrates:
                                              intake -> qualify -> write-back
                                                     |
                                              CRM down? -> retry_queue.py
                                              (backoff, dead-letter — never lost)
```

All CRM calls go through the `CrmAdapter` interface. The repo ships a
simulated in-memory backend — **no network, no API keys, no GHL account
needed**. In production you plug in a real adapter at the one marked seam
(`crm_client.CrmAdapter`), using the exact payload shapes the builders produce
(`PUT /opportunities/{id}`, `POST /contacts/{id}/tasks`).

## File tour

```
gohighlevel-ghl-ai-automation/
├── src/ghl_automation/
│   ├── webhook.py      # parse/validate/normalize inbound GHL payloads
│   ├── qualifier.py    # rule-based lead scoring (hot/warm/cold, never-guess)
│   ├── crm_client.py   # exact GHL payload builders + CrmAdapter + simulated backend
│   ├── pipeline.py     # orchestrates intake -> qualify -> write-back
│   └── retry_queue.py  # backoff retries + dead-letter queue (nothing dropped)
├── tests/
│   ├── test_webhook.py      # valid/missing/malformed/unknown-event payloads
│   ├── test_qualifier.py    # band boundaries, tie-breaking, insufficient-data
│   ├── test_crm_client.py   # exact payload shapes, simulated failure modes
│   ├── test_retry_queue.py  # retry -> recovery, outage -> dead-letter, poison
│   └── test_pipeline.py     # end-to-end routing, CRM-down recovery, rejections
├── requirements.txt    # pytest only — the pipeline itself is stdlib
├── LICENSE             # MIT
└── README.md
```

## 5-minute setup

Verified by running every step below on a clean machine (Python 3.10+).

```bash
# 1. Clone and enter the repo
git clone https://github.com/Vroom-Analytics-Ltd/gohighlevel-ghl-ai-automation.git
cd gohighlevel-ghl-ai-automation

# 2. Create an isolated environment and install the one test dependency
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Run the test suite — expect 57 passed
pytest -q

# 4. Watch one lead flow through the whole pipeline
python3 - <<'EOF'
import json, sys
sys.path.insert(0, "src")
from ghl_automation.pipeline import Pipeline
from ghl_automation.crm_client import SimulatedCrm

webhook_body = json.dumps({
    "type": "appointment_scheduled",
    "contact": {
        "id": "ct_123", "firstName": "Dana", "lastName": "Reyes",
        "email": "dana@example.com", "phone": "+14155550132",
        "tags": ["referral"],
    },
    "opportunity": {"id": "opp_9", "pipelineId": "pipe_1"},
    "customFields": {"budget": "5000"},
})

pipeline = Pipeline(crm=SimulatedCrm())
report = pipeline.process(webhook_body)
print("verdict:", report["verdict"].value, "| score:", report["score"])
print("reasons:")
for reason in report["reasons"]:
    print("  -", reason)
print("writes:", [(w["kind"], w["status"]) for w in report["writes"]])
EOF
```

Expected output:

```
verdict: hot | score: 75
reasons:
  - +10 — reachable by email
  - +15 — reachable by phone
  - +25 — booked an appointment
  - +10 — referred
  - +15 — budget stated (5000)
writes: [('stage_update', 'done'), ('task', 'done')]
```

## Running the tests

```bash
pip install -r requirements.txt && pytest
```

`57 passed`. The suite covers the failure shapes that sink real CRM
automations:

1. **Bad payloads rejected** — malformed JSON, missing fields, unknown event
   types never enter the pipeline or the retry queue.
2. **Never-guess scoring** — a lead with no email and no phone gets
   `insufficient_data` (score `None`), not a made-up number; a human gets a
   manual-review task instead.
3. **Boundary honesty** — scores exactly on a band edge (70 = hot, 40 = warm)
   are deterministic; opt-out tags suppress even hot scores.
4. **CRM down → queued, not lost** — writes retry with exponential backoff and
   complete when the backend recovers; persistent outages end in a visible
   dead-letter list.
5. **Poison never retried forever** — permanently invalid requests go straight
   to dead-letter on the first attempt.

## How this maps to the Vroom offer

This repo is the engineering core of our **AI automation (S01)** service —
the "every new lead lands in your CRM and gets followed up, automatically"
workflow from the Basic package. It demonstrates the three things buyers are
actually paying for: real webhook/API integration with their CRM, an AI step
with guardrails (transparent scoring, human review when data is missing), and
production failure handling (retries, alerts surface, dead-lettering).

## The Vroom page

This is exactly what we build and maintain for clients — see the packages:
**[AI automation & orchestration](https://vroomanalytics.com/automation/)**.

*Page audit (staging, 2026-09-19): the /automation/ page has the packages and
the $99/mo care-plan pitch, plus an interactive demo — but no matching blog
article is linked (the blog has no articles yet). Reported, not changed.*

## Monthly peace of mind

Setup is just day one. The [$99/mo care plan](https://vroomanalytics.com/automation/)
keeps this running — monitoring, fixes, and monthly optimization, so you never
think about it again.

## License

MIT — see [LICENSE](LICENSE). Steal the pattern, keep the hard rules, tell us
what broke.
