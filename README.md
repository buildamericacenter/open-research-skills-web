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
export MYSQL_PASSWORD=<database-password>
export SECRET_KEY=<random-secret-key>
python app.py
```

Open `http://127.0.0.1:5000`.

## Account Database

Registration, login, logout, and profile management use the MySQL `account` table in the `openresearchskills` schema.

Required environment variable:

```text
MYSQL_PASSWORD=<database-password>
```

Optional environment variables:

```text
MYSQL_HOST=billauchpad-umd-db.cndi2fq3ieot.us-east-1.rds.amazonaws.com
MYSQL_PORT=3306
MYSQL_DATABASE=openresearchskills
MYSQL_USER=open_research_dev
SECRET_KEY=<random-secret-key>
```

Passwords are stored as PBKDF2 hashes in the `account.password` column.

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

### Persistent Skill Package Storage on S3

The app can store submitted skill package files in a private AWS S3 bucket while keeping MVP metadata in `data/submitted_skills.json`.

Required Render environment variables:

```text
USE_S3_STORAGE=true
AWS_REGION=us-east-1
S3_BUCKET_NAME=open-research-skills-packages
AWS_ACCESS_KEY_ID=<render-s3-user-access-key-id>
AWS_SECRET_ACCESS_KEY=<render-s3-user-secret-access-key>
```

Use a dedicated low-permission IAM user for Render. The IAM user should only have access to the S3 bucket used by this app. Do not use the AWS root account or an administrator access key.

When S3 storage is enabled, submitted skill files are uploaded under:

```text
s3://open-research-skills-packages/submitted_skills/<skill_id>/
```

Download links use temporary S3 presigned URLs, so the bucket can remain private.

## AWS App Runner Deployment

Repository:

```text
https://github.com/buildamericacenter/open-research-skills-web
```

AWS App Runner settings:

- Source: GitHub repository
- Runtime: `Python 3`
- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:app`
- Port: `8080` or the `PORT` environment variable assigned by App Runner

The Flask entry file is `app.py`, and the Flask application variable is `app`. The app also exposes `application = app` for WSGI compatibility.

The Flask startup reads the `PORT` environment variable:

```python
port = int(os.environ.get("PORT", 5000))
app.run(host="0.0.0.0", port=port)
```

Health check:

```text
/health
```

Local storage note: this MVP keeps uploaded skill packages, submitted metadata, and generated validation files in local folders such as `uploads/`, `data/`, `storage/`, and `output/`. These folders are created automatically if missing.

For production, uploaded files should be moved to AWS S3, and metadata should be moved to a managed database.

## AWS Elastic Beanstalk Deployment

This repository includes a GitHub Actions workflow at `.github/workflows/deploy-eb.yml`.

Elastic Beanstalk environment:

```text
Application: open-research-skills-web
Environment: open-research-skills-web-env
URL: http://open-research-skills-web-env.eba-uxp2iea5.us-east-1.elasticbeanstalk.com
```

Required GitHub Actions repository secrets:

```text
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
AWS_REGION
EB_APPLICATION_NAME
EB_ENVIRONMENT_NAME
```

Expected secret values:

```text
AWS_REGION=us-east-1
EB_APPLICATION_NAME=open-research-skills-web
EB_ENVIRONMENT_NAME=open-research-skills-web-env
```

Required Elastic Beanstalk environment properties:

```text
USE_S3_STORAGE=true
AWS_REGION=us-east-1
S3_BUCKET_NAME=open-research-skills-packages
```

The Elastic Beanstalk EC2 instance profile should have `OpenResearchSkillsS3StoragePolicy` attached so the app can upload submitted skill packages to S3 without storing AWS access keys in the Flask app environment.

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
