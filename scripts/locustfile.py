"""Locust load-test script for the deployed RAG API.

Usage (headless CLI, so you do not need to type the host in the UI):
    pip install locust
    export RAG_API_KEY=rag-gA_rHdtlCnf4Om8gtx4M1D80MaaQQEpCsrcpenJod1g
    locust -f scripts/locustfile.py \
      --headless \
      -u 1 \
      -r 1 \
      -t 10m \
      --host http://rag-alb-1767061209.us-east-1.elb.amazonaws.com

Or use the web UI:
    locust -f scripts/locustfile.py --host http://rag-alb-1767061209.us-east-1.elb.amazonaws.com

Then open http://localhost:8089 and set:
  - Number of users  (e.g. 5, 10, 20)
  - Spawn rate       (e.g. 1, 2, 5)

Notes:
  - Each /query call takes roughly 30-50 seconds depending on the question,
    so keep the run time generous or increase the user count for a quick burst.
  - The Authorization header is set per-request; change RAG_API_KEY if needed.
"""

import os
import random

from locust import HttpUser, between, task


RAG_API_KEY = os.environ.get(
    "RAG_API_KEY",
    # Replace with your real production key if you do not want to set env var.
    "rag-gA_rHdtlCnf4Om8gtx4M1D80MaaQQEpCsrcpenJod1g",
)

# Sample questions that exercise different topics. Edit freely.
SAMPLE_QUESTIONS = [
    "What is a Kubernetes pod?",
    "Explain the difference between a process and a thread.",
    "What is a REST API and how does it work?",
    "How does a database index improve query performance?",
    "What is the purpose of a load balancer in a web application?",
]


class RAGQueryUser(HttpUser):
    """Simulates a user asking questions to the RAG /query endpoint."""

    # Wait 1-5 seconds between requests to mimic real think time.
    # Set to between(0, 0) for a pure stress test.
    wait_time = between(1, 5)

    @task
    def query(self):
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {RAG_API_KEY}",
        }
        question = random.choice(SAMPLE_QUESTIONS)
        payload = {
            "q": question,
            "thread_id": f"locust-{random.randint(1, 1000000)}",
        }
        with self.client.post(
            "/query",
            json=payload,
            headers=headers,
            catch_response=True,
            name="/query",
            timeout=60,
        ) as response:
            if response.status_code == 200:
                try:
                    data = response.json()
                    if data.get("status") == "error":
                        response.failure(f"API error: {data.get('message')}")
                    else:
                        response.success()
                except Exception as exc:
                    response.failure(f"Invalid JSON: {exc}")
            else:
                response.failure(f"HTTP {response.status_code}")
