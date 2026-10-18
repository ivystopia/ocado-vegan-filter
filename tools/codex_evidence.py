"""Isolated, authenticated Codex calls with retained call-level usage.

No API key or paid fallback is used. Authentication remains owned by Codex.
"""

from __future__ import annotations

import contextvars
import json
import os
import subprocess
import tempfile
import threading
import select
import time
import uuid
from pathlib import Path
from typing import Any, Callable

CALL_RECORDS: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar(
    "ocado_call_records", default=None
)
USAGE_JOURNAL: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "ocado_usage_journal", default=None
)
JOURNAL_LOCK = threading.Lock()
ISOLATION_VERSION = "codex-evidence-only-v3"
STOP_FILE: Path | None = None


class UsageLimitError(RuntimeError):
    """Included allowance exhausted: checkpoint instead of retrying/falling back."""


class StopRequestedError(UsageLimitError):
    """Finish/account for active calls, then checkpoint before starting another."""


class IsolationViolationError(RuntimeError):
    """Observed tool use invalidates the invocation and halts further calls."""


def quota_snapshot(codex_bin: str) -> dict[str, Any]:
    """Read quota/auth mode, never returning account identity or credentials."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"OPENAI_API_KEY", "CODEX_API_KEY", "GH_TOKEN", "GITHUB_TOKEN"}
    }
    process = subprocess.Popen(
        [codex_bin, "app-server", "--disable", "apps", "--disable", "plugins", "--disable", "hooks"],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
    )

    def send(payload):
        process.stdin.write((json.dumps(payload) + "\n").encode())
        process.stdin.flush()

    try:
        send(
            {
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": "ocado_quota_reader", "version": "1.0.0"}},
            }
        )
        deadline = time.time() + 20
        pending = b""
        while time.time() < deadline:
            if not select.select([process.stdout], [], [], 1)[0]:
                continue
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                break
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                if not line.strip():
                    continue
                event = json.loads(line)
                if event.get("id") in {1, 2, 3} and "error" in event:
                    raise UsageLimitError(
                        "Account protocol rejected included-plan verification; no fallback."
                    )
                if event.get("id") == 1:
                    send({"method": "initialized"})
                    send({"id": 2, "method": "account/read", "params": {"refreshToken": False}})
                if event.get("id") == 2 and "result" in event:
                    if (event["result"].get("account") or {}).get("type") != "chatgpt":
                        raise UsageLimitError(
                            "ChatGPT sign-in is required; refusing API/other billing authentication."
                        )
                    send({"id": 3, "method": "account/rateLimits/read"})
                if event.get("id") == 3 and "result" in event:
                    result = event["result"]
                    buckets = result.get("rateLimitsByLimitId") or {"codex": result.get("rateLimits", {})}
                    return {
                        "at_epoch": time.time(),
                        "auth_type": "chatgpt",
                        "ordinary_usage_allowed": result.get("ordinaryUsageAllowed"),
                        "buckets": {
                            key: {
                                field: value.get(field)
                                for field in (
                                    "primary",
                                    "secondary",
                                    "credits",
                                    "rateLimitReachedType",
                                    "spendControlReached",
                                )
                            }
                            for key, value in buckets.items()
                        },
                    }
        raise UsageLimitError("Could not verify included-plan quota; refusing an unverified paid fallback.")
    finally:
        process.terminate()
        process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()


def require_included_allowance(snapshot: dict[str, Any]) -> None:
    if snapshot.get("auth_type") != "chatgpt" or snapshot.get("ordinary_usage_allowed") is not True:
        raise UsageLimitError(
            "Included-plan authentication/ordinary usage is unverified or unavailable; checkpointing."
        )
    if not any(
        bucket.get("primary") or bucket.get("secondary") for bucket in snapshot.get("buckets", {}).values()
    ):
        raise UsageLimitError("Included allowance windows are unavailable; refusing an unverified fallback.")
    for bucket in snapshot["buckets"].values():
        for window in (bucket.get("primary"), bucket.get("secondary")):
            if window and window.get("usedPercent", 0) >= 100:
                raise UsageLimitError("Included-plan limit reached; no credit/API fallback.")
        if bucket.get("rateLimitReachedType") or bucket.get("spendControlReached"):
            raise UsageLimitError("Included-plan account limit reached; checkpointing.")
        # Codex can automatically consume purchased credits after exhaustion.
        # With a credit balance, reserve headroom rather than risking that path.
        if (bucket.get("credits") or {}).get("hasCredits") and any(
            window and window.get("usedPercent", 0) >= 90
            for window in (bucket.get("primary"), bucket.get("secondary"))
        ):
            raise UsageLimitError(
                "Included allowance is close to exhaustion with paid credits available; refusing automatic credit use."
            )


def invocation(
    codex_bin: str, directory: Path, schema: Path, output: Path, model: str, effort: str
) -> list[str]:
    command = [
        codex_bin,
        "exec",
        "--ephemeral",
        "--json",
        "--ignore-user-config",
        "--ignore-rules",
        "--strict-config",
        "--skip-git-repo-check",
        "--output-schema",
        str(schema),
        "--output-last-message",
        str(output),
        "-C",
        str(directory),
        "-s",
        "read-only",
        "-m",
        model,
    ]
    # Invocation-scoped settings; never edit the user's personal configuration.
    settings = {
        "model_reasoning_effort": effort,
        "web_search": "disabled",
        "project_doc_max_bytes": 0,
        "forced_login_method": "chatgpt",
        "suppress_unstable_features_warning": True,
        "features.shell_tool": False,
        "features.unified_exec": False,
        "features.apps": False,
        "features.multi_agent": False,
        "agents.enabled": False,
        "features.browser_use": False,
        "features.browser_use_external": False,
        "features.code_mode_host": False,
        "features.hooks": False,
        "features.remote_plugin": False,
        "features.skill_search": False,
        "features.skip_host_skill_discovery": True,
        "features.view_image": False,
        "features.memories": False,
        "features.plugins": False,
        "features.in_app_browser": False,
        "features.image_generation": False,
        "features.goals": False,
        "features.sleep_tool": False,
        "features.shell_snapshot": False,
        "features.skill_mcp_dependency_install": False,
        "features.fast_mode": False,
    }
    for key, value in settings.items():
        command += ["-c", f"{key}={json.dumps(value)}"]
    return [*command, "-"]


def retain_admission(record: dict[str, Any]) -> None:
    """Retain an attempt before invocation, even if the desktop later dies.

    Completion journals still contain one final record per attempt. The small
    sidecar records local admission, not provider completion or zero usage.
    """
    journal = USAGE_JOURNAL.get()
    if journal is None:
        return
    path = journal.with_name(journal.stem + ".admissions" + journal.suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = (
        "call_id",
        "stage",
        "model",
        "reasoning_effort",
        "product_ids",
        "prompt_version",
        "context_hash",
        "isolation_version",
        "started_at_epoch",
    )
    row = {key: record[key] for key in fields}
    row.update(
        {
            "state": "invocation-prepared",
            "usage": None,
            "note": "Reconcile with the completion journal by call_id. Missing completion means interrupted/unfinished usage, never zero tokens or a successful assessment.",
        }
    )
    with JOURNAL_LOCK, path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def run(
    prompt: str,
    schema: dict[str, Any],
    *,
    model: str,
    effort: str,
    codex_bin: str,
    stage: str,
    product_ids: list[str],
    prompt_version: str,
    context_hash: str,
    validator: Callable[[dict[str, Any]], Any] | None = None,
    canonicalizer: Callable[[dict[str, Any]], tuple[dict[str, Any], list[dict[str, Any]]]] | None = None,
) -> dict[str, Any]:
    started = time.time()
    record: dict[str, Any] = {
        "call_id": str(uuid.uuid4()),
        "stage": stage,
        "model": model,
        "reasoning_effort": effort,
        "product_ids": product_ids,
        "prompt_version": prompt_version,
        "context_hash": context_hash,
        "isolation_version": ISOLATION_VERSION,
        "service_tier": "standard",
        "started_at_epoch": started,
        "usage": None,
        "events": [],
        "exit_status": None,
        "raw_response": "",
        "error": None,
        "invocation_started": False,
    }
    try:
        if STOP_FILE is not None and STOP_FILE.exists():
            raise StopRequestedError(
                "Benchmark stop file found; active calls are accounted for, no new calls started."
            )
        record["quota_before"] = quota_snapshot(codex_bin)
        require_included_allowance(record["quota_before"])
        with tempfile.TemporaryDirectory(prefix="ocado-evidence-only-") as tmp:
            directory = Path(tmp)
            schema_path, output_path = directory / "schema.json", directory / "response.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            env = {
                key: value
                for key, value in os.environ.items()
                if key
                not in {
                    "OPENAI_API_KEY",
                    "CODEX_API_KEY",
                    "GH_TOKEN",
                    "GITHUB_TOKEN",
                }
            }
            command = invocation(codex_bin, directory, schema_path, output_path, model, effort)
            record["invocation"] = command
            if STOP_FILE is not None and STOP_FILE.exists():
                raise StopRequestedError("Stop file appeared before invocation; no new model call admitted.")
            retain_admission(record)
            record["invocation_started"] = True
            completed = subprocess.run(
                command, input=prompt, text=True, capture_output=True, env=env, check=False
            )
            record["exit_status"] = completed.returncode
            record["raw_response"] = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
            usage_events = []
            forbidden = []
            for line in completed.stdout.splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                record["events"].append(event)
                if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
                    usage_events.append(event["usage"])
                item = event.get("item", {})
                # Startup diagnostics are not tool executions. Retain the
                # recognised disabled-host warning; reject unknown item kinds.
                startup_warning = item.get("type") == "error" and any(
                    str(item.get("message", "")).startswith(prefix)
                    for prefix in (
                        "Under-development features enabled:",
                        "Code Mode is unavailable because code-mode host is disabled.",
                    )
                )
                if (
                    item.get("type")
                    and item["type"] not in {"agent_message", "reasoning"}
                    and not startup_warning
                ):
                    forbidden.append(item.get("type"))
            if usage_events:
                keys = {key for usage in usage_events for key in usage}
                record["usage"] = {key: sum(usage.get(key, 0) for usage in usage_events) for key in keys}
            error_text = completed.stderr + completed.stdout
            if completed.returncode and any(
                word in error_text.lower()
                for word in (
                    "hit your usage limit",
                    "usage_limit_reached",
                    "insufficient_quota",
                    "usage limit has been reached",
                )
            ):
                raise UsageLimitError(
                    "Codex included-plan usage limit reached; completed calls are retained."
                )
            if forbidden:
                raise IsolationViolationError(f"Evidence-only isolation violated: {sorted(set(forbidden))}")
            if completed.returncode:
                # Retain relevant diagnostics, never environment/authentication data.
                record["diagnostic_tail"] = completed.stderr[-4000:]
                raise RuntimeError(f"Codex exited {completed.returncode}; see retained call diagnostics")
            if not record["raw_response"].strip():
                raise RuntimeError("Codex returned empty output")
            if record["usage"] is None:
                raise RuntimeError("Codex did not report call token usage; refusing an unmetered benchmark")
            payload = json.loads(record["raw_response"])
            record["parsed_response"] = payload
            validated = payload
            repairs = []
            if canonicalizer is not None:
                validated, repairs = canonicalizer(payload)
            if validator is not None:
                validator(validated)
            if repairs:
                record["validated_response"] = validated
                record["reference_canonicalizations"] = repairs
            return record
    except BaseException as exc:
        record["error"] = str(exc) or type(exc).__name__
        raise
    finally:
        record["duration_seconds"] = round(time.time() - started, 4)
        records = CALL_RECORDS.get()
        if records is not None:
            records.append(record)
        journal = USAGE_JOURNAL.get()
        if journal is not None:
            journal.parent.mkdir(parents=True, exist_ok=True)
            with JOURNAL_LOCK, journal.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=True) + "\n")


def usage_totals(calls: list[dict[str, Any]]) -> dict[str, Any]:
    unique = {call["call_id"]: call for call in calls}
    return {
        "calls": len(unique),
        "failed_calls": sum(bool(call.get("error")) for call in unique.values()),
        "guard_rejections": sum(call.get("invocation_started") is False for call in unique.values()),
        "missing_usage_calls": sum(
            call.get("usage") is None and call.get("invocation_started", True) for call in unique.values()
        ),
        **{
            key: sum((call.get("usage") or {}).get(key, 0) for call in unique.values())
            for key in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")
        },
    }
