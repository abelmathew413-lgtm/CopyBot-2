"""
Reads reports/latest.json and injects it into dashboard/template.html,
writing the result to dashboard/index.html -- the file GitHub Pages serves.

Run after main.py on each scheduled run:
    python3 src/main.py
    python3 src/render_dashboard.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "latest.json"
TEMPLATE_PATH = ROOT / "dashboard" / "template.html"
OUTPUT_PATH = ROOT / "dashboard" / "index.html"


def render():
    if not REPORT_PATH.exists():
        raise FileNotFoundError(f"{REPORT_PATH} not found -- run src/main.py first")

    report_json = REPORT_PATH.read_text()
    json.loads(report_json)  # fail loudly here rather than ship broken JSON to the browser

    template = TEMPLATE_PATH.read_text()
    # Use a plain string replace (not str.format) since the JSON itself contains
    # literal curly braces that would break format-style substitution.
    html = template.replace("__REPORT_JSON__", report_json)

    OUTPUT_PATH.write_text(html)
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    render()
