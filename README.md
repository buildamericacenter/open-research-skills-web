# Open Research Skills Platform MVP

A local Flask MVP for publishing, browsing, validating, learning, and managing research skills.

The included executable demo skill is **NPV-DCF Analysis**. It accepts:

- `cash_flows.csv`
- `assumptions.md`
- optional `expected_results.csv`

It produces:

- `npv_results.csv`
- `validation_report.md`

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000`.

## Deploy on Render Free

1. Push this folder to a GitHub repository.
2. In Render, create a new **Web Service** from that repository.
3. Use these settings:
   - Runtime: `Python`
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app`
   - Plan: `Free`
4. Deploy.

The included `render.yaml` also supports Render Blueprint deployment.

Note: Render free services use ephemeral local storage. Uploaded files and generated results can disappear after restarts or redeploys. For persistent uploads, upgrade later and add a persistent disk or object storage.

## Cash Flow Format

The app accepts common column names. Recommended columns:

```csv
period,inflows,outflows
0,0,100000
1,40000,5000
2,45000,5000
3,50000,5000
```

Alternatively, provide `net_cash_flow` directly.

## Assumptions Format

Include a discount rate in Markdown:

```md
# Assumptions

Discount rate: 10%
```
