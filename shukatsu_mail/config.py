"""
config.py
config.toml(絞り込み条件)と環境変数(認証情報)を読み込む。
"""

import os
import tomllib
from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# 日本時間(夏時間が無いので固定オフセットで足りる。Windowsでも tzdata 不要)
JST = timezone(timedelta(hours=9), "JST")

# 企業ノートの「選考フェーズ」と同じ9段階
PHASES = ["エントリー", "書類選考", "一次面接", "二次面接", "三次面接", "最終面接", "内定", "不合格", "辞退"]
# 就活スケジュールの「種別」「形式」
KINDS = ["選考", "インターン", "説明会", "その他"]
FORMATS = ["対面", "オンライン", "その他"]
# 本ツールが就活スケジュールに追加する「ステータス」
STATUS_UNCONFIRMED = "未確認"
STATUS_CONFIRMED = "確認済"
STATUS_APPLIED = "反映済"


@dataclass
class Settings:
    newer_than_days: int
    processed_label: str
    sender_domains: list[str]
    subject_keywords: list[str]
    extra_query: str
    max_body_chars: int
    model: str
    max_mails_per_run: int
    min_interval_seconds: float


def load_settings(path: Path = ROOT / "config.toml") -> Settings:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    gmail = raw["gmail"]
    return Settings(
        newer_than_days=gmail["newer_than_days"],
        processed_label=gmail["processed_label"],
        sender_domains=gmail["sender_domains"],
        subject_keywords=gmail["subject_keywords"],
        extra_query=gmail.get("extra_query", ""),
        max_body_chars=raw["extract"]["max_body_chars"],
        max_mails_per_run=raw["extract"]["max_mails_per_run"],
        min_interval_seconds=raw["extract"]["min_interval_seconds"],
        model=raw["extract"]["model"],
    )


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"環境変数 {name} が設定されていません(.env か GitHub Secrets を確認)")
    return value
