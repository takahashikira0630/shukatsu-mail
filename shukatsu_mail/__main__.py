"""
使い方:
  python -m shukatsu_mail run              # stage と apply を続けて実行(GitHub Actions から呼ぶ)
  python -m shukatsu_mail stage [--dry-run] # 新着メールを「未確認」で追加するだけ
  python -m shukatsu_mail apply [--dry-run] # 「確認済」を企業ノートに反映するだけ
  python -m shukatsu_mail extract FILE...   # .eml / .txt を抽出してJSONを表示(Gmail・Notionに触れない)
  python -m shukatsu_mail setup-notion      # 就活スケジュールに本ツール用の列を追加(最初に1回)
"""

import argparse
import email
import email.policy
import json
import logging
import sys
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import anthropic

from . import notion, pipeline
from .config import JST, load_settings
from .extract import extract
from .gmail import Mail, html_to_text


def load_mail_file(path: Path) -> Mail:
    if path.suffix.lower() == ".eml":
        msg = email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
        part = msg.get_body(preferencelist=("plain", "html"))
        body = part.get_content() if part else ""
        if part and part.get_content_type() == "text/html":
            body = html_to_text(body)
        received = parsedate_to_datetime(msg["Date"]) if msg["Date"] else datetime.now(JST)
        return Mail(id=path.name, subject=str(msg["Subject"] or ""), sender=str(msg["From"] or ""),
                    received_at=received, body=body)
    # .txt は「件名: 」「差出人: 」「受信: YYYY-MM-DD HH:MM」の見出し行 + 空行 + 本文、として読む
    headers: dict[str, str] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    while lines and ":" in lines[0]:
        key, _, value = lines.pop(0).partition(":")
        headers[key.strip()] = value.strip()
    received = datetime.strptime(headers["受信"], "%Y-%m-%d %H:%M").replace(tzinfo=JST) if "受信" in headers else datetime.now(JST)
    return Mail(id=path.name, subject=headers.get("件名", path.stem), sender=headers.get("差出人", ""),
                received_at=received, body="\n".join(lines).strip())


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="shukatsu_mail")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "stage", "apply"):
        sub.add_parser(name).add_argument("--dry-run", action="store_true", help="Notion・Gmailに書き込まない")
    sub.add_parser("extract").add_argument("files", nargs="+", type=Path)
    sub.add_parser("setup-notion")
    args = parser.parse_args()

    if args.command == "setup-notion":
        added = notion.ensure_schedule_properties()
        print("追加した列: " + (", ".join(added) if added else "なし(すべて作成済み)"))
    elif args.command == "extract":
        settings = load_settings()
        client = anthropic.Anthropic()
        for path in args.files:
            result = extract(client, load_mail_file(path), settings)
            print(f"=== {path.name}")
            print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))
    else:
        if args.command in ("run", "stage"):
            pipeline.stage(load_settings(), dry_run=args.dry_run)
        if args.command in ("run", "apply"):
            pipeline.apply(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
