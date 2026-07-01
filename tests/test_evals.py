"""Smoke tests for the eval pipeline (no live API required)."""

import importlib.util
import os
from unittest.mock import MagicMock, patch

# Load eval modules directly from file to avoid importing the whole evals package
# (which pulls in ragas and can fail in a minimal test environment).
_ANGEL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_module(name, rel_path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ANGEL, rel_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pipeline_mod = _load_module("evals.pipeline", "evals/pipeline.py")
guardrails_mod = _load_module("evals.guardrails_eval", "evals/guardrails_eval.py")

run_pipeline = pipeline_mod.run_pipeline
detect_tool = pipeline_mod.detect_tool
compute_guardrails_metrics = guardrails_mod.compute_guardrails_metrics


def test_detect_tool_maps_thought_process():
    assert detect_tool(["Intent: Technical", "Search Term: Redis"]) == "retrieve_documents"
    assert detect_tool(["Intent: Conversational/Memory"]) == "direct_answer"
    assert detect_tool(["Intent: Guardrails Fired"]) == "guardrails"
    assert detect_tool([]) == "unknown"


def test_run_pipeline_populates_actual_response():
    """run_pipeline should call /query and fill actual_response/contexts/tools."""
    golden = {
        "rag_samples": [
            {
                "id": 1,
                "question": "What is Redis?",
                "reference": "Redis is a data store.",
                "relevant_contexts": ["ctx1"],
                "expected_tools": ["retrieve_documents"],
                "actual_response": "",
                "actual_contexts": [],
                "actual_tools_called": [],
            }
        ],
        "guardrails_samples": [],
    }

    post_resp = MagicMock()
    post_resp.raise_for_status.return_value = None
    post_resp.json.return_value = {
        "answer": "Redis is a store.",
        "thought_process": ["Intent: Technical", "Search Term: Redis"],
        "sources": ["ctx1"],
    }

    with patch.object(pipeline_mod.requests, "post", return_value=post_resp) as mock_post:
        result = run_pipeline(golden, progress_callback=None)

    sample = result["rag_samples"][0]
    assert sample["actual_response"] == "Redis is a store."
    assert sample["actual_contexts"] == ["ctx1"]
    assert sample["actual_tools_called"] == ["retrieve_documents"]
    mock_post.assert_called_once()


def test_compute_guardrails_metrics():
    results = [
        {"result": "TP"},
        {"result": "TN"},
        {"result": "FP"},
        {"result": "FN"},
    ]
    metrics = compute_guardrails_metrics(results)
    assert metrics["tp"] == 1
    assert metrics["tn"] == 1
    assert metrics["fp"] == 1
    assert metrics["fn"] == 1
    assert metrics["accuracy"] == 0.5
