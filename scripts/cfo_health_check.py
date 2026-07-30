#!/usr/bin/env python3
"""
CFO Financial Health Check — регулярный мониторинг финансового состояния.
Запускается по cron. Генерирует markdown-отчёт.

Usage:
  python3 cfo_health_check.py                    # standard output
  python3 cfo_health_check.py --post             # also post to Paperclip issue
  python3 cfo_health_check.py --demo             # use demo data
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

# === Configuration ===
COMPANY_ID = "f0b8eb0f-19d8-4893-bbed-7b17b35b5aac"
CFO_AGENT_ID = "91b4a5ad-8f3c-4dd4-8bfd-041b2b99f9b5"
PAPERCLIP_API = "http://localhost:3100"
PAPERCLIP_KEY = os.environ.get("PAPERCLIP_API_KEY", "pcp_83e22c62183fa52725559d65c77b8061bb08ef42a7cddfc3")

# === Health Metrics ===
HEALTH_METRICS = {
    "cash_minimum": 100_000,        # минимальный остаток
    "cash_target": 500_000,         # целевой остаток
    "max_burn_rate": 200_000,       # максимальный burn rate в месяц
    "revenue_target": 1_000_000,    # месячный план по выручке
    "margin_minimum": 0.50,         # минимальная маржа
    "tax_reserve_rate": 0.06,       # резерв под налоги
}


def generate_demo_data() -> dict:
    """Generate demo financial data for testing."""
    return {
        "period": datetime.now(timezone.utc).strftime("%Y-%m"),
        "revenue": {
            "gross": 3_154_874,
            "net": 1_849_613,
            "by_marketplace": {
                "wildberries": 1_362_217,
                "ozon": 943_321,
                "yandex_market": 849_336,
            },
        },
        "costs": {
            "commissions": 355_321,
            "logistics": 519_891,
            "returns": 430_049,
            "advertising": 140_332,
            "salary": 120_000,
            "infrastructure": 15_000,
            "other": 21_457,
        },
        "tax": {
            "system": "USN_income_6%",
            "base": 3_154_874,
            "before_deduction": 189_292,
            "insurance_deduction": 45_842,
            "payable": 143_450,
        },
        "cashflow": {
            "current_balance": 500_000,
            "forecast_30d": 1_149_078,
            "min_balance": 520_428,
            "daily_inflow_avg": 59_574,
            "daily_outflow_avg": 37_815,
        },
        "risks": {
            "marginality": {"level": "low", "value": 0.84, "threshold": 0.50},
            "cash_gap": {"level": "low", "value": 520_428, "threshold": 100_000},
            "diversification": {"level": "low", "value": 3, "threshold": 2},
            "tax_burden": {"level": "info", "value": 143_450, "threshold": 189_292},
        },
        "previous_metrics": {
            "revenue_gross": 2_800_000,
            "margin": 0.81,
            "cashflow_forecast": 980_000,
        },
    }


def analyze_health(data: dict) -> dict:
    """Analyze financial health and return structured assessment."""
    risks = data["risks"]
    cashflow = data["cashflow"]
    revenue = data["revenue"]
    margin = risks["marginality"]["value"]

    # Overall health score (0-100)
    score = 100
    reasons = []

    if margin < HEALTH_METRICS["margin_minimum"]:
        score -= 20
        reasons.append(f"Маржа {margin:.0%} ниже порога {HEALTH_METRICS['margin_minimum']:.0%}")
    else:
        reasons.append(f"Маржа {margin:.0%} — здоровый уровень")

    if cashflow["min_balance"] < HEALTH_METRICS["cash_minimum"]:
        score -= 30
        reasons.append(f"❗ Критический минимум: {cashflow['min_balance']:,.0f} RUB")
    elif cashflow["min_balance"] < HEALTH_METRICS["cash_target"]:
        score -= 10
        reasons.append(f"⚠️ Остаток ниже целевого: {cashflow['min_balance']:,.0f} RUB < {HEALTH_METRICS['cash_target']:,} RUB")
    else:
        reasons.append(f"✅ Остаток {cashflow['min_balance']:,.0f} RUB — выше целевого")

    if revenue["gross"] < HEALTH_METRICS["revenue_target"]:
        score -= 15
        reasons.append(f"Выручка {revenue['gross']:,.0f} RUB ниже плана {HEALTH_METRICS['revenue_target']:,} RUB")
    else:
        reasons.append(f"✅ Выручка {revenue['gross']:,.0f} RUB — план выполнен")

    # Trend analysis
    prev_revenue = data.get("previous_metrics", {}).get("revenue_gross", 0)
    if prev_revenue:
        growth = (revenue["gross"] - prev_revenue) / prev_revenue
        if growth > 0.05:
            reasons.append(f"📈 Рост выручки {growth:.1%} относительно прошлого периода")
        elif growth < -0.05:
            score -= 10
            reasons.append(f"📉 Падение выручки {growth:.1%} — требуется анализ")

    return {
        "score": max(0, min(100, score)),
        "grade": "A" if score >= 80 else "B" if score >= 60 else "C" if score >= 40 else "D",
        "reasons": reasons,
        "critical": score < 50,
    }


def format_report(data: dict, health: dict) -> str:
    """Generate markdown report."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    period = data["period"]
    rev = data["revenue"]
    costs = data["costs"]
    tax = data["tax"]
    cf = data["cashflow"]

    report = f"""# CFO Financial Health Check
**Period:** {period} | **Generated:** {now}

---

## Executive Summary

**Health Score:** {health['score']}/100 ({health['grade']})
**Status:** {'❗ Critical — требует немедленного внимания' if health['critical'] else '✅ В норме'}

### Key Metrics
| Metric | Value |
|--------|------:|
| Gross Revenue | {rev['gross']:,.0f} RUB |
| Net Revenue | {rev['net']:,.0f} RUB |
| Margin | {health['reasons'][0] if 'маржа' in health['reasons'][0].lower() else 'N/A'} |
| Current Balance | {cf['current_balance']:,.0f} RUB |
| 30d Forecast | {cf['forecast_30d']:,.0f} RUB |
| Min Balance | {cf['min_balance']:,.0f} RUB |
| Tax Payable | {tax['payable']:,.0f} RUB |

### Health Checks
{chr(10).join(f'- {r}' for r in health['reasons'])}

---

## Revenue Breakdown
| Marketplace | Gross Revenue |
|------------|-------------:|
"""
    for mp, val in rev.get("by_marketplace", {}).items():
        report += f"| {mp.capitalize()} | {val:,.0f} RUB |\n"

    total_costs = sum(costs.values())
    report += f"""
## Cost Structure
| Category | Amount | % of Revenue |
|---------|------:|:-----------:|
"""
    for cat, val in costs.items():
        pct = val / rev["gross"] * 100 if rev["gross"] else 0
        report += f"| {cat.capitalize()} | {val:,.0f} RUB | {pct:.1f}% |\n"
    report += f"| **Total** | **{total_costs:,.0f} RUB** | **{total_costs/rev['gross']*100:.1f}%** |\n"

    report += f"""
## Tax Summary
- **System:** {tax['system']}
- **Base:** {tax['base']:,.0f} RUB
- **Before Deduction:** {tax['before_deduction']:,.0f} RUB
- **Insurance Deduction:** {tax['insurance_deduction']:,.0f} RUB
- **Payable:** {tax['payable']:,.0f} RUB
- **Effective Rate:** {tax['payable']/tax['base']*100:.2f}%

## Cash Flow Forecast (30 days)
- **Current Balance:** {cf['current_balance']:,.0f} RUB
- **Forecast at +30d:** {cf['forecast_30d']:,.0f} RUB
- **Minimum Balance:** {cf['min_balance']:,.0f} RUB
- **Avg Daily Inflow:** {cf['daily_inflow_avg']:,.0f} RUB
- **Avg Daily Outflow:** {cf['daily_outflow_avg']:,.0f} RUB

## Risk Assessment
"""
    risk_labels = {
        "marginality": "Маржинальность",
        "cash_gap": "Кассовый разрыв",
        "diversification": "Диверсификация",
        "tax_burden": "Налоговая нагрузка",
    }
    for key, risk in data["risks"].items():
        icon = "🟢" if risk["level"] == "low" else "🟡" if risk["level"] == "medium" else "🔴" if risk["level"] == "high" else "ℹ️"
        label = risk_labels.get(key, key)
        report += f"- {icon} **[{risk['level'].upper()}]** {label}: {risk['value']:,.0f} (threshold: {risk['threshold']:,})\n"

    report += f"""
---
*Report generated by CFO Agent | {now}*
"""
    return report


def post_to_paperclip(report: str, run_id: str):
    """Post report as comment to the financial planning issue via curl."""
    body_escaped = report.replace('"', '\\"').replace("'", "''")
    cmd = [
        "curl", "-s", "-X", "POST",
        f"{PAPERCLIP_API}/api/issues/65b29049-62ad-40ec-a74f-98bcef208a53/comments",
        "-H", "Content-Type: application/json",
        "-H", f"Authorization: Bearer {PAPERCLIP_KEY}",
        "-H", f"X-Paperclip-Run-Id: {run_id}",
        "-H", f"X-Paperclip-Agent-Id: {CFO_AGENT_ID}",
        "-d", json.dumps({
            "body": report,
            "authorType": "agent",
        }),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            output = result.stdout.strip()
            if "error" in output.lower():
                print(f"⚠️ API post error: {output[:200]}")
                return False
            print("✅ Posted to Paperclip")
            return True
        else:
            print(f"⚠️ curl failed: {result.stderr[:200]}")
            return False
    except Exception as e:
        print(f"⚠️ Post failed: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="CFO Financial Health Check")
    parser.add_argument("--post", action="store_true", help="Post report to Paperclip")
    parser.add_argument("--demo", action="store_true", help="Use demo data")
    parser.add_argument("--run-id", default="2a15b828-a13e-4734-99aa-925cc3922bac", help="Paperclip run ID")
    args = parser.parse_args()

    # Get data (demo mode for now)
    data = generate_demo_data()
    health = analyze_health(data)
    report = format_report(data, health)

    print(report)

    if args.post:
        post_to_paperclip(report, args.run_id)

    # Return exit code based on health
    if health["critical"]:
        sys.exit(2)
    return 0


if __name__ == "__main__":
    main()
