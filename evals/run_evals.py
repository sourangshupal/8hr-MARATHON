"""
Headless evaluation runner.

Loads the golden dataset, calls the live FastAPI /query endpoint for each sample,
runs guardrails tests, and writes a JSON report to evals/report.json.

Usage:
    python -m evals.run_evals

Requires the FastAPI backend to be running on localhost:8000 (or BACKEND_URL).
"""

import json
import os
import sys

import logfire

# Allow running from repo root without package installation.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()
logfire.configure(token=os.getenv("LOGFIRE_TOKEN"), service_name="evals")

from evals.guardrails_eval import compute_guardrails_metrics, run_guardrails_eval
from evals.pipeline import load_golden_dataset, run_pipeline


def _print_progress(i: int, total: int, label: str, item: str):
    pct = int((i / total) * 100)
    print(f"[{pct:3d}%] {label}: {item[:80]}")


def main() -> dict:
    """Run the full live eval suite and return the report."""
    golden = load_golden_dataset()

    print("🚀 Running live RAG pipeline...")
    enriched = run_pipeline(
        golden,
        progress_callback=lambda i, total, question, stage, response="": _print_progress(i, total, stage, question),
    )

    print("🛡️ Running guardrails tests...")
    guardrails_results = run_guardrails_eval(
        enriched["guardrails_samples"],
        progress_callback=lambda i, total, input_text: _print_progress(i, total, "guardrails", input_text),
    )
    guardrails_metrics = compute_guardrails_metrics(guardrails_results)

    report = {
        "rag_samples": enriched["rag_samples"],
        "guardrails_results": guardrails_results,
        "guardrails_metrics": guardrails_metrics,
    }

    report_path = os.path.join(os.path.dirname(__file__), "report.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\n✅ Report saved to {report_path}")
    print(
        f"🛡️ Guardrails — correct: {guardrails_metrics['correct']}/{guardrails_metrics['total']}, "
        f"precision: {guardrails_metrics['precision']}, recall: {guardrails_metrics['recall']}, "
        f"accuracy: {guardrails_metrics['accuracy']}"
    )

    return report


if __name__ == "__main__":
    main()
