import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from flask import Flask, flash, redirect, render_template, request, send_from_directory, url_for
from werkzeug.utils import secure_filename

from npv_dcf import run_analysis


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "storage" / "uploads"
RESULT_DIR = BASE_DIR / "storage" / "results"

app = Flask(__name__)
app.config["SECRET_KEY"] = "local-mvp-secret-key"
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024


MODULES = [
    {
        "name": "Publish / Contribute Skills",
        "slug": "publish",
        "summary": "Submit research methods, data checks, and executable skill packages.",
    },
    {
        "name": "Skill Library",
        "slug": "library",
        "summary": "Browse reusable research skills with inputs, outputs, and validation status.",
    },
    {
        "name": "Validation",
        "slug": "validation",
        "summary": "Run skills against expected results and inspect generated reports.",
    },
    {
        "name": "User Account Management",
        "slug": "account",
        "summary": "Local profile placeholder for future login, roles, and saved runs.",
    },
]


def save_upload(field: str, run_dir: Path, required: bool = True) -> Path | None:
    uploaded = request.files.get(field)
    if uploaded is None or uploaded.filename == "":
        if required:
            raise ValueError(f"{field} is required.")
        return None
    filename = secure_filename(uploaded.filename)
    if not filename:
        raise ValueError(f"{field} has an invalid filename.")
    path = run_dir / filename
    uploaded.save(path)
    return path


@app.context_processor
def inject_globals():
    return {"modules": MODULES}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/library")
def library():
    skills = [
        {
            "name": "NPV-DCF Analysis",
            "status": "Executable MVP",
            "description": "Upload cash flows and assumptions to calculate NPV, IRR, and validation artifacts.",
            "href": url_for("npv_dcf_skill"),
        }
    ]
    return render_template("library.html", skills=skills)


@app.route("/skills/npv-dcf", methods=["GET", "POST"])
def npv_dcf_skill():
    if request.method == "POST":
        run_id = datetime.utcnow().strftime("%Y%m%d%H%M%S") + "-" + uuid4().hex[:8]
        upload_dir = UPLOAD_DIR / run_id
        result_dir = RESULT_DIR / run_id
        upload_dir.mkdir(parents=True, exist_ok=True)
        result_dir.mkdir(parents=True, exist_ok=True)

        try:
            cash_flows_path = save_upload("cash_flows", upload_dir)
            assumptions_path = save_upload("assumptions", upload_dir)
            expected_path = save_upload("expected_results", upload_dir, required=False)
            result = run_analysis(cash_flows_path, assumptions_path, expected_path, result_dir)
        except Exception as exc:
            flash(str(exc), "error")
            return redirect(url_for("npv_dcf_skill"))

        return render_template("results.html", result=result, run_id=run_id)

    return render_template("skill_npv.html")


@app.route("/validation")
def validation():
    return render_template("validation.html")


@app.route("/publish")
def publish():
    return render_template("publish.html")


@app.route("/account")
def account():
    return render_template("account.html")


@app.route("/download/<run_id>/<filename>")
def download(run_id: str, filename: str):
    safe_run_id = secure_filename(run_id)
    safe_filename = secure_filename(filename)
    return send_from_directory(RESULT_DIR / safe_run_id, safe_filename, as_attachment=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
