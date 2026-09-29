"""Quản lý prompt `day13-chat` trên project Langfuse cá nhân.

    python scripts/prompt_versions.py status
    python scripts/prompt_versions.py create             # v1 (baseline, production) + v2 (candidate)
    python scripts/prompt_versions.py set-production 2   # promote v2
    python scripts/prompt_versions.py set-production 1   # rollback về v1
    python scripts/prompt_versions.py run --label baseline --label candidate

Key được đọc từ file env ở thư mục gốc repo; script không bao giờ in key.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import find_dotenv, load_dotenv

from app.cli import configure_utf8_stdio

PROMPT_V1 = "Feature={{feature}}\nDocs={{docs}}\nQuestion={{message}}"
# v2: thay đổi nhỏ về format/độ dài câu trả lời, vẫn giữ đủ ba biến của contract.
PROMPT_V2 = (
    "Feature={{feature}}\nDocs={{docs}}\nQuestion={{message}}\n"
    "Answer in at most 3 short bullet points and cite the doc you used."
)
DEFAULT_MESSAGE = "Explain why metrics traces and logs work together"


def _client():
    from langfuse import get_client

    return get_client()


def _prompt_name() -> str:
    return os.getenv("LANGFUSE_PROMPT_NAME", "day13-chat")


def status() -> None:
    client = _client()
    name = _prompt_name()
    versions = []
    version = 1
    while True:
        try:
            prompt = client.api.prompts.get(name, version=version)
        except Exception:
            break
        versions.append(prompt)
        version += 1
    if not versions:
        print(f"Prompt '{name}' chưa tồn tại trong project.")
        return
    for prompt in versions:
        labels = ", ".join(label for label in prompt.labels if label != "latest") or "-"
        text = prompt.prompt.replace("\n", " | ")
        print(f"{name} v{prompt.version}  labels=[{labels}]  prompt: {text}")


def create() -> None:
    client = _client()
    name = _prompt_name()
    try:
        client.api.prompts.get(name, version=1)
    except Exception:
        pass
    else:
        print(f"Prompt '{name}' đã tồn tại, không tạo lại để tránh sinh version thừa.")
        status()
        return
    v1 = client.create_prompt(
        name=name,
        prompt=PROMPT_V1,
        labels=["baseline", "production"],
        type="text",
        commit_message="v1: prompt contract gốc",
    )
    v2 = client.create_prompt(
        name=name,
        prompt=PROMPT_V2,
        labels=["candidate"],
        type="text",
        commit_message="v2: giới hạn 3 bullet và trích dẫn doc",
    )
    print(f"Đã tạo {name} v{v1.version} và v{v2.version}.")
    status()


def set_production(version: int) -> None:
    client = _client()
    name = _prompt_name()
    current = client.api.prompts.get(name, version=version)
    labels = [label for label in current.labels if label != "latest"]
    if "production" not in labels:
        labels.append("production")
    # Label là duy nhất trong một prompt: gán `production` cho version này sẽ gỡ nó khỏi version cũ.
    client.update_prompt(name=name, version=version, new_labels=labels)
    print(f"Đã chuyển label production sang {name} v{version}.")
    status()


def run(labels: list[str], message: str) -> None:
    import httpx

    from app.main import app

    async def send(label: str) -> None:
        os.environ["LANGFUSE_PROMPT_LABEL"] = label
        correlation_id = f"req-{uuid.uuid4().hex[:8]}"
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://prompt-run") as http:
            response = await http.post(
                "/chat",
                headers={"x-request-id": correlation_id},
                json={
                    "user_id": "u-prompt-run",
                    "session_id": "s-prompt-run",
                    "feature": "qa",
                    "message": message,
                },
            )
        body = response.json()
        print(
            f"label={label:<10} status={response.status_code} correlation_id={correlation_id} "
            f"tokens_in={body.get('tokens_in')}"
        )

    for label in labels:
        asyncio.run(send(label))
    _client().flush()
    print("Xem trace: python scripts/find_trace.py <correlation_id>")


def main() -> None:
    configure_utf8_stdio()
    load_dotenv(find_dotenv(usecwd=True))
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("create")
    promote = sub.add_parser("set-production")
    promote.add_argument("version", type=int)
    run_parser = sub.add_parser("run", help="Chạy cùng một input với từng label (in-process)")
    run_parser.add_argument("--label", action="append", required=True)
    run_parser.add_argument("--message", default=DEFAULT_MESSAGE)
    args = parser.parse_args()

    if args.command == "status":
        status()
    elif args.command == "create":
        create()
    elif args.command == "set-production":
        set_production(args.version)
    else:
        run(args.label, args.message)


if __name__ == "__main__":
    main()
