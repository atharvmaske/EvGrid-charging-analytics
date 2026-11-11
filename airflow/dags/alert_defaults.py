import os
from datetime import timedelta

import requests

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")


def _notify_slack_failure(context):
    if not SLACK_WEBHOOK_URL:
        return

    task_id = context["task_instance"].task_id
    dag_id = context["dag"].dag_id
    execution_date = context["ts"]
    log_url = context["task_instance"].log_url

    message = {
        "text": (
            f":red_circle: *Airflow task failed*\n"
            f"*DAG:* `{dag_id}`\n"
            f"*Task:* `{task_id}`\n"
            f"*When:* {execution_date}\n"
            f"<{log_url}|View logs>"
        )
    }

    try:
        requests.post(SLACK_WEBHOOK_URL, json=message, timeout=10)
    except Exception as exc:
        print(f"Slack alert failed to send: {exc}")


def default_args(retries=2, retry_delay_minutes=5, execution_timeout_minutes=None, **overrides):
    args = {
        "owner": "evgrid",
        "retries": retries,
        "retry_delay": timedelta(minutes=retry_delay_minutes),
        "on_failure_callback": _notify_slack_failure,
    }
    if execution_timeout_minutes is not None:
        args["execution_timeout"] = timedelta(minutes=execution_timeout_minutes)
    args.update(overrides)
    return args