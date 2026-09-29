"""Log -> Trace: tìm trace Langfuse theo correlation_id và in waterfall các span.

    python scripts/find_trace.py req-1a2b3c4d req-5e6f7a8b
    python scripts/find_trace.py --slowest 3      # 3 request chậm nhất trong data/logs.jsonl
    python scripts/find_trace.py --failed 3       # 3 request lỗi gần nhất

Trace được tìm qua metadata `correlation_id` (được propagate từ middleware vào trace),
nên không cần ghi trace_id vào log. Key được đọc từ file env ở thư mục gốc; không in key.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import find_dotenv, load_dotenv

from app.cli import configure_utf8_stdio

LOG_PATH = Path("data/logs.jsonl")


def _as_dict(item) -> dict:
    return item if isinstance(item, dict) else item.dict()


def ids_from_logs(event: str, count: int, slowest: bool) -> list[str]:
    rows = [json.loads(line) for line in LOG_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [r for r in rows if r.get("event") == event and r.get("correlation_id")]
    if slowest:
        rows.sort(key=lambda r: r.get("latency_ms") or 0, reverse=True)
    else:
        rows.reverse()
    for r in rows[:count]:
        detail = f"latency_ms={r.get('latency_ms')}" if slowest else f"error_type={r.get('error_type')}"
        print(f"log: {r['ts']} {event} correlation_id={r['correlation_id']} {detail}")
    return [r["correlation_id"] for r in rows[:count]]


def find_trace_id(client, correlation_id: str, attempts: int = 6) -> str | None:
    flt = json.dumps(
        [{"type": "stringObject", "column": "metadata", "key": "correlation_id",
          "operator": "=", "value": correlation_id}]
    )
    for _ in range(attempts):  # ingestion is asynchronous; retry briefly
        data = client.api.observations.get_many(filter=flt, is_root_observation=True, limit=1).data
        if data:
            return _as_dict(data[0])["traceId"]
        time.sleep(3)
    return None


def print_waterfall(client, trace_id: str) -> None:
    data = client.api.observations.get_many(
        trace_id=trace_id, limit=50, fields="core,basic,usage,model,prompt", expand_metadata="true"
    ).data
    obs = sorted((_as_dict(d) for d in data), key=lambda d: (d["startTime"], not d.get("isRootObservation")))
    if not obs:
        print("  (chưa có observation)")
        return
    t0 = obs[0]["startTime"]
    for d in obs:
        start_ms = (d["startTime"] - t0).total_seconds() * 1000
        dur_ms = (d["endTime"] - d["startTime"]).total_seconds() * 1000 if d.get("endTime") else float("nan")
        indent = "  " if d.get("isRootObservation") else "    └─ "
        line = f"{indent}{d['name']:<15} {d['type']:<10} +{start_ms:6.0f}ms {dur_ms:7.0f}ms level={d['level']}"
        if d.get("statusMessage"):
            line += f" status='{d['statusMessage']}'"
        if d["type"] == "GENERATION":
            line += (f" model={d.get('model')} usage={d.get('usageDetails')} cost={d.get('totalCost')}"
                     f" prompt={d.get('promptName')} v{d.get('promptVersion')}")
        print(line)


def main() -> None:
    configure_utf8_stdio()
    load_dotenv(find_dotenv(usecwd=True))
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("correlation_ids", nargs="*")
    parser.add_argument("--slowest", type=int, default=0)
    parser.add_argument("--failed", type=int, default=0)
    args = parser.parse_args()

    ids = list(args.correlation_ids)
    if args.slowest:
        ids += ids_from_logs("response_sent", args.slowest, slowest=True)
    if args.failed:
        ids += ids_from_logs("request_failed", args.failed, slowest=False)
    if not ids:
        parser.error("cần ít nhất một correlation_id, --slowest hoặc --failed")

    from langfuse import get_client

    client = get_client()
    for correlation_id in ids:
        trace_id = find_trace_id(client, correlation_id)
        print(f"\n{correlation_id} -> trace_id={trace_id}")
        if trace_id:
            print_waterfall(client, trace_id)


if __name__ == "__main__":
    main()
