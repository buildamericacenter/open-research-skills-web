import csv
import math
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CashFlowRow:
    period: int
    inflows: float
    outflows: float
    net_cash_flow: float
    discount_factor: float
    present_value: float


@dataclass
class AnalysisResult:
    discount_rate: float
    rows: list[CashFlowRow]
    npv: float
    irr: float | None
    validation: list[dict[str, str]]
    results_csv: Path
    report_md: Path


def parse_discount_rate(path: Path) -> float:
    text = path.read_text(encoding="utf-8")
    patterns = [
        r"discount\s*rate\s*[:=\-]\s*([0-9]+(?:\.[0-9]+)?)\s*%",
        r"discount\s*rate\s*[:=\-]\s*([0-9]+(?:\.[0-9]+)?)",
        r"\brate\s*[:=\-]\s*([0-9]+(?:\.[0-9]+)?)\s*%",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = float(match.group(1))
            return value / 100 if "%" in match.group(0) or value > 1 else value
    raise ValueError("Could not find a discount rate in assumptions.md. Use a line like 'Discount rate: 10%'.")


def _float_value(row: dict[str, str], names: tuple[str, ...], default: float = 0.0) -> float:
    normalized = {key.strip().lower().replace(" ", "_"): value for key, value in row.items()}
    for name in names:
        if name in normalized and str(normalized[name]).strip() != "":
            raw = str(normalized[name]).strip().replace("$", "").replace(",", "")
            if raw.startswith("(") and raw.endswith(")"):
                raw = f"-{raw[1:-1]}"
            return float(raw)
    return default


def _period_value(row: dict[str, str], fallback: int) -> int:
    value = _float_value(row, ("period", "year", "time", "t"), fallback)
    return int(value)


def read_cash_flows(path: Path, discount_rate: float) -> list[CashFlowRow]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("cash_flows.csv must include a header row.")
        rows = list(reader)

    if not rows:
        raise ValueError("cash_flows.csv must include at least one cash flow row.")

    cash_flows: list[CashFlowRow] = []
    for index, row in enumerate(rows):
        period = _period_value(row, index)
        inflows = _float_value(row, ("inflows", "inflow", "cash_inflow", "revenue", "benefits"))
        outflows = _float_value(row, ("outflows", "outflow", "cash_outflow", "costs", "expenses", "capex"))
        explicit_net = _float_value(
            row,
            ("net_cash_flow", "net_cashflow", "net", "cash_flow", "cashflow", "free_cash_flow", "fcf"),
            math.nan,
        )
        net_cash_flow = explicit_net if not math.isnan(explicit_net) else inflows - outflows
        discount_factor = 1 / ((1 + discount_rate) ** period)
        present_value = net_cash_flow * discount_factor
        cash_flows.append(
            CashFlowRow(
                period=period,
                inflows=inflows,
                outflows=outflows,
                net_cash_flow=net_cash_flow,
                discount_factor=discount_factor,
                present_value=present_value,
            )
        )
    return cash_flows


def calculate_npv(rows: list[CashFlowRow]) -> float:
    return sum(row.present_value for row in rows)


def _npv_at_rate(values: list[float], rate: float) -> float:
    return sum(value / ((1 + rate) ** index) for index, value in enumerate(values))


def calculate_irr(rows: list[CashFlowRow]) -> float | None:
    values = [row.net_cash_flow for row in sorted(rows, key=lambda item: item.period)]
    if not any(value < 0 for value in values) or not any(value > 0 for value in values):
        return None

    candidates = [rate / 1000 for rate in range(-999, 5001)]
    previous_rate = candidates[0]
    previous_value = _npv_at_rate(values, previous_rate)
    bracket: tuple[float, float] | None = None

    for rate in candidates[1:]:
        value = _npv_at_rate(values, rate)
        if value == 0:
            return rate
        if previous_value * value < 0:
            bracket = (previous_rate, rate)
            break
        previous_rate = rate
        previous_value = value

    if bracket is None:
        return None

    low, high = bracket
    for _ in range(100):
        mid = (low + high) / 2
        low_value = _npv_at_rate(values, low)
        mid_value = _npv_at_rate(values, mid)
        if abs(mid_value) < 1e-8:
            return mid
        if low_value * mid_value <= 0:
            high = mid
        else:
            low = mid
    return (low + high) / 2


def compare_expected(expected_path: Path | None, actual: dict[str, float]) -> list[dict[str, str]]:
    if expected_path is None or not expected_path.exists():
        return [{"metric": "expected_results.csv", "expected": "not provided", "actual": "not compared", "status": "Skipped"}]

    expected: dict[str, float] = {}
    with expected_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            metric = (row.get("metric") or row.get("name") or "").strip().lower()
            value = row.get("expected") or row.get("value") or row.get("result")
            if metric and value is not None:
                expected[metric] = float(str(value).replace("%", "").strip()) / (100 if "%" in str(value) else 1)

    comparisons: list[dict[str, str]] = []
    for metric, actual_value in actual.items():
        expected_value = expected.get(metric)
        if expected_value is None:
            status = "Missing expected value"
            expected_display = "not provided"
        else:
            tolerance = 0.01 if metric == "npv" else 0.0001
            status = "Pass" if abs(expected_value - actual_value) <= tolerance else "Review"
            expected_display = f"{expected_value:.6f}"
        comparisons.append(
            {
                "metric": metric.upper(),
                "expected": expected_display,
                "actual": f"{actual_value:.6f}",
                "status": status,
            }
        )
    return comparisons


def write_results(rows: list[CashFlowRow], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["period", "inflows", "outflows", "net_cash_flow", "discount_factor", "present_value"])
        for row in rows:
            writer.writerow(
                [
                    row.period,
                    f"{row.inflows:.2f}",
                    f"{row.outflows:.2f}",
                    f"{row.net_cash_flow:.2f}",
                    f"{row.discount_factor:.6f}",
                    f"{row.present_value:.2f}",
                ]
            )


def write_report(result: AnalysisResult) -> None:
    lines = [
        "# NPV-DCF Validation Report",
        "",
        f"- Discount rate: {result.discount_rate:.4%}",
        f"- NPV: {result.npv:,.2f}",
        f"- IRR: {'Not available' if result.irr is None else f'{result.irr:.4%}'}",
        "",
        "## Validation",
        "",
        "| Metric | Expected | Actual | Status |",
        "| --- | ---: | ---: | --- |",
    ]
    for item in result.validation:
        lines.append(f"| {item['metric']} | {item['expected']} | {item['actual']} | {item['status']} |")
    result.report_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_analysis(
    cash_flows_path: Path,
    assumptions_path: Path,
    expected_path: Path | None,
    output_dir: Path,
) -> AnalysisResult:
    discount_rate = parse_discount_rate(assumptions_path)
    rows = read_cash_flows(cash_flows_path, discount_rate)
    npv = calculate_npv(rows)
    irr = calculate_irr(rows)
    actual = {"npv": npv}
    if irr is not None:
        actual["irr"] = irr

    results_csv = output_dir / "npv_results.csv"
    report_md = output_dir / "validation_report.md"
    validation = compare_expected(expected_path, actual)
    result = AnalysisResult(discount_rate, rows, npv, irr, validation, results_csv, report_md)
    write_results(rows, results_csv)
    write_report(result)
    return result
