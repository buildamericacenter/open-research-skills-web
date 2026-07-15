import os
import csv
import json
import re
import zipfile
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from uuid import uuid4

import pymysql
from flask import Flask, Response, flash, redirect, render_template, request, send_from_directory, session, url_for
from pymysql.cursors import DictCursor
from pymysql.err import IntegrityError, MySQLError
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from npv_dcf import run_analysis
from s3_storage import presigned_download_url, safe_s3_path, s3_bucket_name, upload_directory_to_s3, use_s3_storage


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "storage" / "uploads"
RESULT_DIR = BASE_DIR / "storage" / "results"
SKILL_DIR = BASE_DIR / "storage" / "skills"
SKILL_INDEX = SKILL_DIR / "skills.json"
DATA_DIR = BASE_DIR / "data"
SUBMITTED_SKILLS_INDEX = DATA_DIR / "submitted_skills.json"
SUBMITTED_SKILLS_UPLOAD_DIR = BASE_DIR / "uploads" / "submitted_skills"
VALIDATION_UPLOAD_DIR = BASE_DIR / "uploads" / "validation_runs"
VALIDATION_OUTPUT_DIR = BASE_DIR / "output"

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "local-mvp-secret-key")
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
application = app

MYSQL_HOST = os.environ.get("MYSQL_HOST", "billauchpad-umd-db.cndi2fq3ieot.us-east-1.rds.amazonaws.com")
MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
MYSQL_DATABASE = os.environ.get("MYSQL_DATABASE", "openresearchskills")
MYSQL_USER = os.environ.get("MYSQL_USER", "open_research_dev")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "")


MODULES = [
    {
        "name": "Publish / Contribute Skills",
        "slug": "publish",
        "label": "Publish",
        "summary": "Submit reusable research skill packages.",
    },
    {
        "name": "Skill Library",
        "slug": "library",
        "label": "Library",
        "summary": "Browse executable methods and examples.",
    },
    {
        "name": "Validation",
        "slug": "validation",
        "label": "Validation",
        "summary": "Check outputs against expected results.",
    },
    {
        "name": "Demo / Education Videos",
        "slug": "videos",
        "label": "Education",
        "summary": "Host short walkthroughs and teaching notes.",
    },
    {
        "name": "User Account Management",
        "slug": "account",
        "label": "Account",
        "summary": "Manage profiles, roles, and saved work.",
    },
]


class AccountRepository:
    def connection(self):
        if not MYSQL_PASSWORD:
            raise RuntimeError("MYSQL_PASSWORD is not configured.")
        return pymysql.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            database=MYSQL_DATABASE,
            cursorclass=DictCursor,
            autocommit=True,
            connect_timeout=8,
        )

    def find_by_email(self, email: str) -> dict[str, object] | None:
        with self.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT * FROM account WHERE email = %s LIMIT 1", (email.lower(),))
                return cursor.fetchone()

    def find_by_id(self, account_id: int) -> dict[str, object] | None:
        with self.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT * FROM account WHERE account_id = %s LIMIT 1", (account_id,))
                return cursor.fetchone()

    def create(self, full_name: str, email: str, password_hash: str) -> int:
        with self.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO account (full_name, email, password) VALUES (%s, %s, %s)",
                    (full_name, email.lower(), password_hash),
                )
                return int(cursor.lastrowid)

    def update_profile(self, account_id: int, full_name: str, email: str, password_hash: str | None = None) -> None:
        with self.connection() as conn:
            with conn.cursor() as cursor:
                if password_hash:
                    cursor.execute(
                        "UPDATE account SET full_name = %s, email = %s, password = %s WHERE account_id = %s",
                        (full_name, email.lower(), password_hash, account_id),
                    )
                else:
                    cursor.execute(
                        "UPDATE account SET full_name = %s, email = %s WHERE account_id = %s",
                        (full_name, email.lower(), account_id),
                    )


account_repository = AccountRepository()


def password_hash(password: str) -> str:
    return generate_password_hash(password, method="pbkdf2:sha256", salt_length=8)


def current_account() -> dict[str, object] | None:
    account_id = session.get("account_id")
    if not account_id:
        return None
    try:
        account = account_repository.find_by_id(int(account_id))
    except (RuntimeError, MySQLError):
        session.pop("account_id", None)
        return None
    if account is None:
        session.pop("account_id", None)
    return account


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if current_account() is None:
            flash("Please log in to manage your account.", "error")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped_view


def display_initials(full_name: str) -> str:
    parts = [part[0].upper() for part in full_name.split() if part]
    return "".join(parts[:2]) or "A"


def ensure_local_storage() -> None:
    for folder in [
        UPLOAD_DIR,
        RESULT_DIR,
        SKILL_DIR,
        DATA_DIR,
        SUBMITTED_SKILLS_UPLOAD_DIR,
        VALIDATION_UPLOAD_DIR,
        VALIDATION_OUTPUT_DIR,
    ]:
        folder.mkdir(parents=True, exist_ok=True)
    if not SKILL_INDEX.exists():
        SKILL_INDEX.write_text("[]", encoding="utf-8")
    if not SUBMITTED_SKILLS_INDEX.exists():
        SUBMITTED_SKILLS_INDEX.write_text("[]", encoding="utf-8")


ensure_local_storage()

SAMPLE_SKILLS = [
    {
        "id": "npv-dcf",
        "name": "NPV-DCF Analysis",
        "research_type": "Empirical / Financial Analysis",
        "description": "Calculates NPV, IRR, and investment decision from cash-flow inputs.",
        "input_type": "CSV + assumptions file",
        "output_type": "results CSV + validation report",
        "validation_status": "Validated",
        "author": "Open Research Skills Team",
        "version": "1.0",
        "purpose": "Evaluate investment value from discounted cash-flow data.",
        "procedure": "Upload cash flows, parse assumptions, calculate net cash flow, NPV, IRR, and compare expected results.",
        "validation_standard": "Validated against expected NPV and IRR values when expected_results.csv is provided.",
        "example_files": ["cash_flows.csv", "assumptions.md", "expected_results.csv"],
        "executable": True,
    },
    {
        "id": "bidding-collusion-coding",
        "name": "Bidding Collusion Document Coding",
        "research_type": "Qualitative Analysis",
        "description": "Extracts and codes collusion behavior from court judgment documents.",
        "input_type": "legal text document",
        "output_type": "structured coding table",
        "validation_status": "Tested",
        "author": "Open Research Skills Team",
        "version": "0.1",
        "purpose": "Support structured qualitative coding of collusion evidence.",
        "procedure": "Upload legal text, identify actors and behaviors, assign codes, and export a coding table.",
        "validation_standard": "Tested on sample judgment excerpts with manual review.",
        "example_files": ["judgment.txt", "coding_schema.md"],
        "executable": False,
    },
    {
        "id": "literature-screening-theme-coding",
        "name": "Literature Screening and Theme Coding",
        "research_type": "Literature Review",
        "description": "Screens papers and extracts research themes using predefined criteria.",
        "input_type": "paper list / abstracts",
        "output_type": "screening table + theme summary",
        "validation_status": "Draft",
        "author": "Open Research Skills Team",
        "version": "0.1",
        "purpose": "Make literature screening criteria explicit and reusable.",
        "procedure": "Load paper records, apply inclusion criteria, code themes, and summarize findings.",
        "validation_standard": "Draft workflow pending inter-coder agreement testing.",
        "example_files": ["abstracts.csv", "screening_criteria.md"],
        "executable": False,
    },
    {
        "id": "case-study-evidence-extraction",
        "name": "Case Study Evidence Extraction",
        "research_type": "Case Study",
        "description": "Extracts timeline, actors, decisions, and evidence from case documents.",
        "input_type": "case documents",
        "output_type": "evidence table + case summary",
        "validation_status": "Draft",
        "author": "Open Research Skills Team",
        "version": "0.1",
        "purpose": "Organize case evidence into a transparent analytical record.",
        "procedure": "Review documents, extract timeline events, identify actors, and compile evidence.",
        "validation_standard": "Draft workflow pending case-level replication tests.",
        "example_files": ["case_documents.zip", "extraction_template.csv"],
        "executable": False,
    },
    {
        "id": "optimization-model-formulation",
        "name": "Optimization Model Formulation",
        "research_type": "Optimization",
        "description": "Converts a planning problem into decision variables, objective function, and constraints.",
        "input_type": "problem description",
        "output_type": "model formulation",
        "validation_status": "Draft",
        "author": "Open Research Skills Team",
        "version": "0.1",
        "purpose": "Translate planning problems into formal optimization models.",
        "procedure": "Identify decisions, define objective, list constraints, and produce a structured formulation.",
        "validation_standard": "Draft workflow pending benchmark problem review.",
        "example_files": ["problem_statement.md", "formulation_template.md"],
        "executable": False,
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


def unique_folder(folder: Path) -> Path:
    if not folder.exists():
        return folder
    counter = 2
    while True:
        candidate = folder.with_name(f"{folder.name}-{counter}")
        if not candidate.exists():
            return candidate
        counter += 1


def safe_folder_name(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower()).strip("-")
    return cleaned or "submitted-skill"


def load_submitted_skills() -> list[dict[str, str]]:
    if not SUBMITTED_SKILLS_INDEX.exists():
        return []
    try:
        return json.loads(SUBMITTED_SKILLS_INDEX.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def save_submitted_skills(skills: list[dict[str, str]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SUBMITTED_SKILLS_INDEX.write_text(json.dumps(skills, indent=2), encoding="utf-8")


def submitted_skill_folder(skill_name: str) -> tuple[str, Path]:
    base_name = safe_folder_name(skill_name)
    folder_name = base_name
    counter = 2
    while (SUBMITTED_SKILLS_UPLOAD_DIR / folder_name).exists():
        folder_name = f"{base_name}-{counter}"
        counter += 1
    folder = SUBMITTED_SKILLS_UPLOAD_DIR / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    return folder_name, folder


def safe_extract_zip(zip_path: Path, destination: Path) -> None:
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            member_path = destination / member.filename
            if not member_path.resolve().is_relative_to(destination.resolve()):
                raise ValueError("Uploaded zip contains an unsafe file path.")
        archive.extractall(destination)


def find_skill_md(folder: Path) -> Path | None:
    for path in folder.rglob("*"):
        if path.is_file() and path.name.lower() == "skill.md":
            return path
    return None


def parse_front_matter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    match = re.match(r"^---\s*\n(.*?)\n---\s*", text, flags=re.DOTALL)
    if not match:
        return {}
    metadata: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip().lower()] = value.strip().strip("\"'")
    return metadata


def extract_heading_section(text: str, heading: str) -> str:
    pattern = rf"^##\s+{re.escape(heading)}\s*$([\s\S]*?)(?=^##\s+|\Z)"
    match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def extract_skill_metadata(text: str) -> dict[str, str]:
    front_matter = parse_front_matter(text)
    title_match = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
    purpose = extract_heading_section(text, "Purpose")
    inputs = extract_heading_section(text, "Inputs")
    outputs = extract_heading_section(text, "Outputs")
    validation = extract_heading_section(text, "Validation")

    metadata = {
        "name": front_matter.get("name") or (title_match.group(1).strip() if title_match else ""),
        "description": front_matter.get("description") or purpose,
        "research_type": front_matter.get("research_type", ""),
        "input_type": front_matter.get("required_inputs") or front_matter.get("inputs") or inputs,
        "output_type": front_matter.get("expected_outputs") or front_matter.get("outputs") or outputs,
        "validation_standard": front_matter.get("validation_standard") or validation,
        "author": front_matter.get("author", ""),
        "version": front_matter.get("version") or "0.1",
        "license": front_matter.get("license") or "Not specified",
        "purpose": purpose,
        "procedure": extract_heading_section(text, "Procedure") or extract_heading_section(text, "Procedure Summary"),
    }
    return metadata


def detect_package_files(folder: Path, skill_md_path: Path) -> dict[str, object]:
    paths = [path for path in folder.rglob("*") if path.is_file()]
    folder_names = {path.name.lower() for path in folder.rglob("*") if path.is_dir()}
    file_names = [str(path.relative_to(folder)) for path in paths]
    return {
        "has_skill_md": skill_md_path.exists(),
        "has_input": "input" in folder_names,
        "has_references": "references" in folder_names,
        "has_expected": "expected" in folder_names,
        "has_output": "output" in folder_names,
        "has_validation_report": any(path.name.lower() == "validation_report.md" for path in paths),
        "files": file_names,
    }


def prepare_skill_package(uploaded) -> dict[str, object]:
    if uploaded is None or uploaded.filename == "":
        raise ValueError("No file is uploaded.")

    original_filename = secure_filename(uploaded.filename)
    suffix = Path(original_filename).suffix.lower()
    if suffix not in {".zip", ".md"}:
        raise ValueError("Uploaded file must be .zip or .md.")
    if suffix == ".md" and original_filename.lower() != "skill.md":
        raise ValueError("For .md uploads, the file must be named SKILL.md.")

    staging_id = f"_staging-{uuid4().hex[:10]}"
    staging_folder = SUBMITTED_SKILLS_UPLOAD_DIR / staging_id
    staging_folder.mkdir(parents=True, exist_ok=True)
    uploaded_path = staging_folder / original_filename
    uploaded.save(uploaded_path)

    extract_folder = staging_folder / "package"
    extract_folder.mkdir(exist_ok=True)
    if suffix == ".zip":
        try:
            safe_extract_zip(uploaded_path, extract_folder)
        except zipfile.BadZipFile as exc:
            raise ValueError("Uploaded zip file cannot be read.") from exc
    else:
        (extract_folder / "SKILL.md").write_bytes(uploaded_path.read_bytes())

    skill_md_path = find_skill_md(extract_folder)
    if skill_md_path is None:
        raise ValueError("SKILL.md cannot be found in the uploaded package.")

    try:
        skill_text = skill_md_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("SKILL.md cannot be read as UTF-8 text.") from exc

    metadata = extract_skill_metadata(skill_text)
    detected = detect_package_files(extract_folder, skill_md_path)
    return {
        "staging_id": staging_id,
        "uploaded_file_name": original_filename,
        "metadata": metadata,
        "detected": detected,
    }


def finalize_staged_skill(staging_id: str, skill_name: str) -> tuple[str, Path]:
    if not re.fullmatch(r"_staging-[a-f0-9]{10}", staging_id):
        raise ValueError("Uploaded package could not be found. Please upload again.")
    staging_folder = SUBMITTED_SKILLS_UPLOAD_DIR / staging_id
    if not staging_folder.exists():
        raise ValueError("Uploaded package could not be found. Please upload again.")
    destination = unique_folder(SUBMITTED_SKILLS_UPLOAD_DIR / safe_folder_name(skill_name))
    staging_folder.rename(destination)
    return destination.name, destination


def files_for_skill(folder: Path) -> list[dict[str, str]]:
    package_folder = folder / "package"
    root = package_folder if package_folder.exists() else folder
    return [
        {"filename": str(path.relative_to(root))}
        for path in root.rglob("*")
        if path.is_file()
    ]


def original_package_path(folder: Path, uploaded_file_name: str) -> Path | None:
    if not uploaded_file_name:
        return None
    path = folder / secure_filename(uploaded_file_name)
    return path if path.exists() else None


def attach_s3_storage(skill: dict[str, object], folder: Path) -> None:
    if not use_s3_storage():
        return

    s3_prefix = safe_s3_path("submitted_skills", str(skill["id"]))
    upload_directory_to_s3(folder, s3_prefix)
    package_path = original_package_path(folder, str(skill.get("uploaded_file_name", "")))
    if package_path is None:
        package_path = next((path for path in folder.iterdir() if path.is_file()), None)

    skill["storage"] = "s3"
    skill["s3_bucket"] = s3_bucket_name()
    skill["s3_prefix"] = s3_prefix
    if package_path is not None:
        skill["s3_package_key"] = safe_s3_path(s3_prefix, package_path.relative_to(folder).as_posix())


def parse_number(value: str) -> float:
    raw = str(value).strip().replace(",", "").replace("%", "")
    if raw.startswith("(") and raw.endswith(")"):
        raw = f"-{raw[1:-1]}"
    return float(raw)


def read_metric_csv(path: Path, value_column: str) -> tuple[dict[str, dict[str, str]], str | None]:
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                return {}, "CSV file must include a header row."
            normalized_fields = {field.strip().lower(): field for field in reader.fieldnames}
            if "metric" not in normalized_fields or value_column not in normalized_fields:
                return {}, f"CSV must include metric and {value_column} columns."

            metric_field = normalized_fields["metric"]
            value_field = normalized_fields[value_column]
            rows: dict[str, dict[str, str]] = {}
            for row in reader:
                metric = row.get(metric_field, "").strip()
                if metric:
                    rows[metric.lower()] = {
                        "metric": metric,
                        "value": row.get(value_field, "").strip(),
                        "unit": row.get(normalized_fields.get("unit", ""), "").strip() if "unit" in normalized_fields else "",
                        "notes": row.get(normalized_fields.get("notes", ""), "").strip() if "notes" in normalized_fields else "",
                        "tolerance": row.get(normalized_fields.get("tolerance", ""), "").strip() if "tolerance" in normalized_fields else "",
                    }
            return rows, None
    except Exception as exc:
        return {}, f"CSV could not be parsed: {exc}"


def write_validation_report(result: dict[str, object], report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Validation Report",
        "",
        f"- Skill type: {result['skill_type']}",
        f"- Validation source type: {result['validation_source_type']}",
        f"- Status: {result['status']}",
        f"- Metrics checked: {result['total_checked']}",
        "",
        "## Passed Items",
    ]
    passed = result.get("passed_items", [])
    failed = result.get("failed_items", [])
    missing = result.get("missing_metrics", [])
    lines.extend([f"- {item}" for item in passed] or ["- None"])
    lines.append("")
    lines.append("## Failed Items")
    lines.extend([f"- {item}" for item in failed] or ["- None"])
    lines.append("")
    lines.append("## Missing Items")
    lines.extend([f"- {item}" for item in missing] or ["- None"])
    lines.append("")
    lines.append("## Final Decision")
    lines.append(str(result["explanation"]))
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def validate_numerical_output(output_file: Path, validation_source_file: Path, validation_source_type: str) -> dict[str, object]:
    actual_rows, actual_error = read_metric_csv(output_file, "value")
    expected_rows, expected_error = read_metric_csv(validation_source_file, "expected_value")
    report_path = VALIDATION_OUTPUT_DIR / "validation_report.md"

    if actual_error or expected_error:
        result = {
            "skill_type": "Numerical",
            "validation_source_type": validation_source_type,
            "status": "Needs Review",
            "total_checked": 0,
            "passed_count": 0,
            "failed_count": 0,
            "missing_metrics": [error for error in [actual_error, expected_error] if error],
            "passed_items": [],
            "failed_items": [],
            "explanation": "Required data is missing or cannot be parsed.",
            "report_path": report_path,
        }
        write_validation_report(result, report_path)
        return result

    passed_items: list[str] = []
    failed_items: list[str] = []
    missing_metrics: list[str] = []

    for metric_key, expected in expected_rows.items():
        actual = actual_rows.get(metric_key)
        metric_name = expected["metric"]
        if actual is None:
            missing_metrics.append(metric_name)
            continue

        if metric_name.lower() == "decision":
            if actual["value"].strip().lower() == expected["value"].strip().lower():
                passed_items.append(metric_name)
            else:
                failed_items.append(f"{metric_name}: actual '{actual['value']}' != expected '{expected['value']}'")
            continue

        try:
            actual_value = parse_number(actual["value"])
            expected_value = parse_number(expected["value"])
            tolerance = parse_number(expected["tolerance"]) if expected["tolerance"] else 0.0
        except ValueError:
            failed_items.append(f"{metric_name}: numeric value could not be parsed")
            continue

        if abs(actual_value - expected_value) <= tolerance:
            passed_items.append(metric_name)
        else:
            failed_items.append(
                f"{metric_name}: actual {actual_value:g}, expected {expected_value:g}, tolerance {tolerance:g}"
            )

    total_checked = len(passed_items) + len(failed_items)
    if missing_metrics:
        status = "Needs Review"
        explanation = "Some required metrics were missing."
    elif failed_items:
        status = "Invalidated"
        explanation = "One or more required metrics failed validation."
    else:
        status = "Validated"
        explanation = "All required metrics passed validation."

    result = {
        "skill_type": "Numerical",
        "validation_source_type": validation_source_type,
        "status": status,
        "total_checked": total_checked,
        "passed_count": len(passed_items),
        "failed_count": len(failed_items),
        "missing_metrics": missing_metrics,
        "passed_items": passed_items,
        "failed_items": failed_items,
        "explanation": explanation,
        "report_path": report_path,
    }
    write_validation_report(result, report_path)
    return result


def validate_skill_output(skill_type: str, output_file: Path, validation_source_file: Path, validation_source_type: str) -> dict[str, object]:
    if skill_type != "Numerical":
        report_path = VALIDATION_OUTPUT_DIR / "validation_report.md"
        result = {
            "skill_type": skill_type,
            "validation_source_type": validation_source_type,
            "status": "Needs Review",
            "total_checked": 0,
            "passed_count": 0,
            "failed_count": 0,
            "missing_metrics": [],
            "passed_items": [],
            "failed_items": [],
            "explanation": "Automatic validation for this skill type is coming soon.",
            "report_path": report_path,
        }
        write_validation_report(result, report_path)
        return result
    return validate_numerical_output(output_file, validation_source_file, validation_source_type)


def load_contributed_skills() -> list[dict[str, str]]:
    if not SKILL_INDEX.exists():
        return []
    try:
        return json.loads(SKILL_INDEX.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def save_contributed_skills(skills: list[dict[str, str]]) -> None:
    SKILL_DIR.mkdir(parents=True, exist_ok=True)
    SKILL_INDEX.write_text(json.dumps(skills, indent=2), encoding="utf-8")


def save_skill_files(skill_id: str) -> list[dict[str, str]]:
    skill_file_dir = SKILL_DIR / skill_id / "files"
    skill_file_dir.mkdir(parents=True, exist_ok=True)
    saved_files: list[dict[str, str]] = []
    for uploaded in request.files.getlist("skill_files"):
        if uploaded.filename == "":
            continue
        filename = secure_filename(uploaded.filename)
        if not filename:
            continue
        uploaded.save(skill_file_dir / filename)
        saved_files.append({"filename": filename})
    return saved_files


def normalize_skill(skill: dict[str, str], contributed: bool = False) -> dict[str, str]:
    normalized = {
        "id": skill["id"],
        "name": skill["name"],
        "research_type": skill.get("research_type") or "Contributed Skill",
        "description": skill.get("description", ""),
        "input_type": skill.get("input_type") or skill.get("inputs") or "User-defined inputs",
        "output_type": skill.get("output_type") or skill.get("outputs") or "User-defined outputs",
        "validation_status": skill.get("validation_status") or skill.get("status") or "Draft",
        "author": skill.get("author", "Anonymous"),
        "version": skill.get("version", "0.1"),
        "purpose": skill.get("purpose") or skill.get("description", ""),
        "procedure": skill.get("procedure") or "Procedure summary will be added by the contributor.",
        "validation_standard": skill.get("validation_standard") or "Validation standard not yet specified.",
        "example_files": skill.get("example_files", []),
        "files": skill.get("files", []),
        "folder": skill.get("folder", ""),
        "uploaded_file_name": skill.get("uploaded_file_name", ""),
        "storage": skill.get("storage", "local"),
        "s3_package_key": skill.get("s3_package_key", ""),
        "s3_prefix": skill.get("s3_prefix", ""),
        "s3_bucket": skill.get("s3_bucket", ""),
        "created_at": skill.get("created_at", "Sample"),
        "contributed": contributed,
        "executable": skill.get("executable", False),
    }
    normalized["detail_href"] = url_for("library_skill_detail", skill_id=normalized["id"])
    normalized["use_href"] = url_for("use_skill", skill_id=normalized["id"])
    normalized["comment_href"] = f"{normalized['detail_href']}#comments"
    normalized["package_href"] = url_for("download_skill_package", skill_id=normalized["id"])
    normalized["status_class"] = normalized["validation_status"].lower().replace(" ", "-").replace("/", "-")
    return normalized


def all_library_skills() -> list[dict[str, str]]:
    sample_skills = [normalize_skill(skill) for skill in SAMPLE_SKILLS]
    contributed = [normalize_skill(skill, contributed=True) for skill in load_contributed_skills()]
    submitted = [normalize_skill(skill, contributed=True) for skill in load_submitted_skills()]
    return sample_skills + contributed + submitted


def find_library_skill(skill_id: str) -> dict[str, str] | None:
    return next((skill for skill in all_library_skills() if skill["id"] == skill_id), None)


@app.context_processor
def inject_globals():
    account = current_account()
    return {
        "modules": MODULES,
        "current_account": account,
        "current_account_initials": display_initials(str(account["full_name"])) if account else "",
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    return {"status": "ok"}


@app.route("/library")
def library():
    skills = all_library_skills()
    featured_ids = {"npv-dcf", "bidding-collusion-coding", "literature-screening-theme-coding"}
    featured_skills = [skill for skill in skills if skill["id"] in featured_ids]
    research_types = sorted({skill["research_type"] for skill in skills})
    validation_statuses = sorted({skill["validation_status"] for skill in skills})
    input_types = sorted({skill["input_type"] for skill in skills})
    output_types = sorted({skill["output_type"] for skill in skills})
    return render_template(
        "library.html",
        skills=skills,
        featured_skills=featured_skills,
        research_types=research_types,
        validation_statuses=validation_statuses,
        input_types=input_types,
        output_types=output_types,
    )


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


@app.route("/validation", methods=["GET", "POST"])
def validation():
    if request.method == "POST":
        skill_type = request.form.get("skill_type", "Numerical")
        validation_source_type = request.form.get("validation_source_type", "Expected Results File")
        output_upload = request.files.get("skill_output_file")
        source_upload = request.files.get("validation_source_file")

        if output_upload is None or output_upload.filename == "":
            return render_template("validation.html", error="Skill Output File is required.", result=None), 400
        if source_upload is None or source_upload.filename == "":
            return render_template("validation.html", error="Validation Source File is required.", result=None), 400

        run_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + uuid4().hex[:8]
        run_dir = VALIDATION_UPLOAD_DIR / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        output_path = run_dir / secure_filename(output_upload.filename)
        source_path = run_dir / secure_filename(source_upload.filename)
        output_upload.save(output_path)
        source_upload.save(source_path)

        result = validate_skill_output(skill_type, output_path, source_path, validation_source_type)
        return render_template("validation.html", error=None, result=result)

    return render_template("validation.html", error=None, result=None)


@app.route("/publish", methods=["GET", "POST"])
def publish():
    if request.method == "POST":
        action = request.form.get("action", "preview")
        if action == "preview":
            try:
                preview = prepare_skill_package(request.files.get("skill_package"))
            except ValueError as exc:
                return render_template("publish.html", error=str(exc), preview=None), 400
            return render_template("publish.html", error=None, preview=preview)

        staging_id = request.form.get("staging_id", "").strip()
        uploaded_file_name = request.form.get("uploaded_file_name", "").strip()
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        research_type = request.form.get("research_type", "").strip()
        input_type = request.form.get("input_type", "").strip()
        output_type = request.form.get("output_type", "").strip()
        validation_standard = request.form.get("validation_standard", "").strip()
        author = request.form.get("author", "").strip()
        version = request.form.get("version", "").strip() or "0.1"
        license_name = request.form.get("license", "").strip() or "Not specified"
        skill_status = request.form.get("skill_status", "Draft").strip() or "Draft"
        visibility = request.form.get("visibility", "Private Draft").strip() or "Private Draft"

        if not name:
            return render_template("publish.html", error="Skill name could not be detected. Please add it before submitting.", preview=None), 400

        folder_name, folder = finalize_staged_skill(staging_id, name)
        submitted_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        skill = {
            "id": folder_name,
            "name": name,
            "description": description,
            "short_description": description,
            "author": author or "Anonymous",
            "research_type": research_type or "Contributed Skill",
            "purpose": request.form.get("purpose", "").strip() or description,
            "input_type": input_type or "User-defined inputs",
            "output_type": output_type or "User-defined outputs",
            "procedure": request.form.get("procedure", "").strip(),
            "validation_standard": validation_standard or "Not specified",
            "validation_status": skill_status,
            "visibility": visibility,
            "status": skill_status,
            "version": version,
            "license": license_name,
            "uploaded_file_name": uploaded_file_name,
            "created_at": submitted_at,
            "submitted_at": submitted_at,
            "folder": folder_name,
            "files": files_for_skill(folder),
        }
        try:
            attach_s3_storage(skill, folder)
        except Exception as exc:
            return render_template("publish.html", error=f"Skill was parsed, but S3 upload failed: {exc}", preview=None), 500

        skills = load_submitted_skills()
        skills.insert(0, skill)
        save_submitted_skills(skills)
        return redirect(url_for("submission_confirmation", skill_id=folder_name))

    return render_template("publish.html", error=None, preview=None)


@app.route("/publish/confirmation/<skill_id>")
def submission_confirmation(skill_id: str):
    skill = next((item for item in load_submitted_skills() if item["id"] == skill_id), None)
    if skill is None:
        flash("Submission not found.", "error")
        return redirect(url_for("publish"))
    return render_template("submission_confirmation.html", skill=skill)


@app.route("/my-submitted-skills")
def my_submitted_skills():
    return render_template("my_submitted_skills.html", skills=load_submitted_skills())


@app.route("/library/skills/<skill_id>")
def library_skill_detail(skill_id: str):
    skill = find_library_skill(skill_id)
    if skill is None:
        flash("Skill not found.", "error")
        return redirect(url_for("library"))
    return render_template("skill_detail.html", skill=skill)


@app.route("/skills/contributed/<skill_id>")
def skill_detail(skill_id: str):
    return redirect(url_for("library_skill_detail", skill_id=skill_id))


@app.route("/skills/use/<skill_id>")
def use_skill(skill_id: str):
    skill = find_library_skill(skill_id)
    if skill is None:
        flash("Skill not found.", "error")
        return redirect(url_for("library"))
    if skill_id == "npv-dcf":
        return redirect(url_for("npv_dcf_skill"))
    flash("This skill is listed for review. Execution will be added in a later MVP iteration.", "success")
    return redirect(url_for("library_skill_detail", skill_id=skill_id))


@app.route("/skills/package/<skill_id>")
def download_skill_package(skill_id: str):
    skill = find_library_skill(skill_id)
    if skill is None:
        flash("Skill not found.", "error")
        return redirect(url_for("library"))
    if skill.get("storage") == "s3" and skill.get("s3_package_key"):
        return redirect(presigned_download_url(skill["s3_package_key"], skill.get("uploaded_file_name") or f"{skill['id']}.zip"))
    if skill.get("folder") and skill.get("uploaded_file_name"):
        folder = SUBMITTED_SKILLS_UPLOAD_DIR / secure_filename(skill["folder"])
        package_path = original_package_path(folder, skill["uploaded_file_name"])
        if package_path is not None:
            return send_from_directory(folder, package_path.name, as_attachment=True)

    lines = [
        f"# {skill['name']}",
        "",
        f"- Research type: {skill['research_type']}",
        f"- Validation status: {skill['validation_status']}",
        f"- Author: {skill['author']}",
        f"- Version: {skill['version']}",
        "",
        "## Purpose",
        skill["purpose"],
        "",
        "## Required Inputs",
        skill["input_type"],
        "",
        "## Expected Outputs",
        skill["output_type"],
        "",
        "## Procedure Summary",
        skill["procedure"],
        "",
        "## Validation Standard",
        skill["validation_standard"],
        "",
    ]
    content = "\n".join(lines)
    filename = secure_filename(f"{skill['name'].lower().replace(' ', '-')}-package.md")
    return Response(
        content,
        mimetype="text/markdown",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/skills/contributed/<skill_id>/files/<path:filename>")
def skill_file(skill_id: str, filename: str):
    safe_skill_id = secure_filename(skill_id)
    return send_from_directory(SKILL_DIR / safe_skill_id / "files", filename, as_attachment=True)


@app.route("/uploads/submitted_skills/<skill_id>/<path:filename>")
def submitted_skill_file(skill_id: str, filename: str):
    safe_skill_id = secure_filename(skill_id)
    skill = next((item for item in load_submitted_skills() if item["id"] == safe_skill_id), None)
    if skill and skill.get("storage") == "s3" and skill.get("s3_prefix"):
        s3_key = safe_s3_path(skill["s3_prefix"], "package", filename)
        return redirect(presigned_download_url(s3_key, Path(filename).name))

    folder = SUBMITTED_SKILLS_UPLOAD_DIR / safe_skill_id
    package_folder = folder / "package"
    base = package_folder if package_folder.exists() else folder
    return send_from_directory(base, filename, as_attachment=True)


@app.route("/videos")
def videos():
    return render_template("videos.html")


@app.route("/account")
@login_required
def account():
    account_data = current_account()
    my_skills = [
        skill for skill in all_library_skills()
        if str(skill.get("author", "")).strip().lower() in {
            str(account_data["full_name"]).strip().lower(),
            str(account_data["email"]).strip().lower(),
        }
    ]
    return render_template(
        "account.html",
        account=account_data,
        initials=display_initials(str(account_data["full_name"])) if account_data else "A",
        my_skills=my_skills,
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_account():
        return redirect(url_for("account"))

    form = {"full_name": "", "email": ""}
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        form = {"full_name": full_name, "email": email}

        if not full_name or not email or not password:
            return render_template("register.html", error="Name, email, and password are required.", form=form), 400
        if "@" not in email:
            return render_template("register.html", error="Please enter a valid email address.", form=form), 400
        if len(password) < 8:
            return render_template("register.html", error="Password must be at least 8 characters.", form=form), 400
        if password != confirm_password:
            return render_template("register.html", error="Passwords do not match.", form=form), 400

        try:
            existing = account_repository.find_by_email(email)
            if existing:
                return render_template("register.html", error="An account with this email already exists.", form=form), 409
            account_id = account_repository.create(full_name, email, password_hash(password))
        except IntegrityError:
            return render_template("register.html", error="An account with this email already exists.", form=form), 409
        except (RuntimeError, MySQLError) as exc:
            return render_template("register.html", error=f"Account database is unavailable: {exc}", form=form), 503

        session.clear()
        session["account_id"] = account_id
        flash("Account created successfully.", "success")
        return redirect(url_for("account"))

    return render_template("register.html", error=None, form=form)


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_account():
        return redirect(url_for("account"))

    form = {"email": ""}
    next_url = request.args.get("next") or request.form.get("next") or url_for("account")
    if not next_url.startswith("/"):
        next_url = url_for("account")

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        form = {"email": email}
        if not email or not password:
            return render_template("login.html", error="Email and password are required.", form=form, next_url=next_url), 400

        try:
            account_data = account_repository.find_by_email(email)
        except (RuntimeError, MySQLError) as exc:
            return render_template("login.html", error=f"Account database is unavailable: {exc}", form=form, next_url=next_url), 503

        if account_data is None or not check_password_hash(str(account_data["password"]), password):
            return render_template("login.html", error="Invalid email or password.", form=form, next_url=next_url), 401

        session.clear()
        session["account_id"] = int(account_data["account_id"])
        flash("Signed in successfully.", "success")
        return redirect(next_url)

    return render_template("login.html", error=None, form=form, next_url=next_url)


@app.route("/logout")
def logout():
    session.clear()
    flash("Signed out successfully.", "success")
    return redirect(url_for("login"))


@app.route("/account/profile", methods=["POST"])
@login_required
def update_account_profile():
    account_data = current_account()
    account_id = int(account_data["account_id"])
    full_name = request.form.get("full_name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")

    if not full_name or not email:
        flash("Name and email are required.", "error")
        return redirect(url_for("account"))
    if "@" not in email:
        flash("Please enter a valid email address.", "error")
        return redirect(url_for("account"))
    if password and len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return redirect(url_for("account"))
    if password != confirm_password:
        flash("Passwords do not match.", "error")
        return redirect(url_for("account"))

    try:
        existing = account_repository.find_by_email(email)
        if existing and int(existing["account_id"]) != account_id:
            flash("Another account already uses that email.", "error")
            return redirect(url_for("account"))
        account_repository.update_profile(account_id, full_name, email, password_hash(password) if password else None)
    except IntegrityError:
        flash("Another account already uses that email.", "error")
        return redirect(url_for("account"))
    except (RuntimeError, MySQLError) as exc:
        flash(f"Account database is unavailable: {exc}", "error")
        return redirect(url_for("account"))

    flash("Profile updated.", "success")
    return redirect(url_for("account"))


@app.route("/download/<run_id>/<filename>")
def download(run_id: str, filename: str):
    safe_run_id = secure_filename(run_id)
    safe_filename = secure_filename(filename)
    return send_from_directory(RESULT_DIR / safe_run_id, safe_filename, as_attachment=True)


@app.route("/output/<filename>")
def output_file(filename: str):
    return send_from_directory(VALIDATION_OUTPUT_DIR, secure_filename(filename), as_attachment=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
