"""
pipeline.py
1. stage: 新着メール → Geminiで抽出 → 就活スケジュールに「未確認」で追加 → メールに処理済みラベル
2. apply: 本人が「確認済」にしたエントリを企業ノートに反映(企業の紐付け・選考フェーズ更新)→「反映済」
"""

import logging
import os
import time
import unicodedata
from dataclasses import dataclass

from google.genai import errors as genai_errors

from . import company, gmail, notion
from .config import Settings
from .extract import Extraction, ExtractionError, extract, make_client, parse_local_datetime

log = logging.getLogger(__name__)

BODY_EXCERPT_CHARS = 1900
# 公開リポジトリの Actions ログは誰でも読めるので、件名・企業名・エラー本文を伏せる
IN_CI = os.environ.get("GITHUB_ACTIONS") == "true"


def shown(text: object) -> str:
    return "(非表示)" if IN_CI else str(text)

BODY_EXCERPT_BLOCKS = 3


@dataclass
class Entry:
    """就活スケジュールに書き込む1行分。Notionに書く前の形で持ち、テストしやすくする。"""
    title: str
    kind: str
    start: str | None
    end: str | None
    format: str | None
    location: str | None
    todo: str | None
    company_page_id: str | None
    extracted_company: str | None
    extracted_phase: str | None
    needs_review: bool
    note: str


def to_notion_date(value: str | None) -> str | None:
    """'YYYY-MM-DD' はそのまま、'YYYY-MM-DDTHH:MM' は日本時間のオフセットを付ける。"""
    if not value:
        return None
    parsed = parse_local_datetime(value)
    if "T" in value:
        return parsed.strftime("%Y-%m-%dT%H:%M:00+09:00")
    return parsed.isoformat()


def build_entries(result: Extraction, companies: list[dict]) -> list[Entry]:
    if not result.is_job_related:
        return []

    reasons = list(dict.fromkeys(result.review_reasons))  # 重複を除いて順序は保つ
    needs_review = result.needs_review
    matched = company.find_match(result.company_name or "", companies)
    if not result.company_name:
        needs_review = True
        reasons.append("企業名を読み取れませんでした")
    elif matched is None:
        needs_review = True
        reasons.append(f"企業ノートに「{result.company_name}」が見つかりません(確認済にすると新規作成します。既存企業なら「企業」列で紐付けてください)")

    note_lines = [f"経由: {result.via_service}"] if result.via_service else []
    note_lines += [f"・{r}" for r in reasons]
    note = "\n".join(note_lines)
    name = matched["name"] if matched else (result.company_name or "企業不明")
    common = dict(
        company_page_id=matched["id"] if matched else None,
        extracted_company=result.company_name,
        extracted_phase=result.phase,
        needs_review=needs_review,
        note=note,
    )

    if not result.events:
        # 「書類選考通過」のように、予定は無くてもフェーズが変わる連絡
        if result.phase is None:
            return []
        return [Entry(title=f"{name}|選考フェーズ: {result.phase}", kind="選考", start=None, end=None,
                      format=None, location=None, todo=None, **common)]

    return [
        Entry(
            title=f"{name}|{event.title}",
            kind=event.kind,
            start=to_notion_date(event.start),
            end=to_notion_date(event.end),
            format=event.format,
            location=event.location_or_url,
            todo=event.todo,
            **common,
        )
        for event in result.events
    ]


def body_excerpt(mail: gmail.Mail) -> list[str]:
    head = f"件名: {mail.subject}\n差出人: {mail.sender}\n受信: {mail.received_at:%Y-%m-%d %H:%M}"
    body = mail.body[: BODY_EXCERPT_CHARS * BODY_EXCERPT_BLOCKS]
    chunks = [body[i:i + BODY_EXCERPT_CHARS] for i in range(0, len(body), BODY_EXCERPT_CHARS)]
    return [head] + chunks


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()


def is_probably_ad(mail: gmail.Mail, settings: Settings) -> bool:
    """一斉配信か営業メールの多い差出人からで、件名に選考の連絡らしい言葉が無いメール。Gemini に送らずに飛ばす。"""
    sender = _fold(mail.sender)
    from_ad_sender = any(_fold(s) in sender for s in settings.ad_senders)
    subject = _fold(mail.subject)
    has_important_word = any(_fold(k) in subject for k in settings.important_keywords)
    return (mail.bulk or from_ad_sender) and not has_important_word


def stage(settings: Settings, dry_run: bool = False) -> None:
    service = gmail.build_service()
    client = make_client()
    label_id = None if dry_run else gmail.ensure_label(service, settings.processed_label)
    skipped_label_id = None if dry_run else gmail.ensure_label(service, settings.skipped_label)
    companies = notion.list_companies()

    message_ids = gmail.list_new_message_ids(service, settings)
    log.info("対象メール: %d 件(今回 Gemini に送るのは古い順に最大 %d 件)", len(message_ids), settings.max_mails_per_run)
    last_call = 0.0
    calls = skipped = would_send = 0
    stopped = False  # 件数の上限か回数制限に達したら、それ以降は Gemini を呼ばない
    for message_id in message_ids:
        if calls >= settings.max_mails_per_run:
            stopped = True
        if stopped and not dry_run:
            break
        # まずヘッダーだけで広告かどうかを判定し、Gemini に送るものだけ本文を取る
        mail = gmail.get_message(service, message_id, with_body=False)
        if is_probably_ad(mail, settings):
            skipped += 1
            if not dry_run:
                gmail.add_label(service, mail.id, skipped_label_id)
            continue
        if stopped:
            # dry-run では、残りのメールも「飛ばす/送る」の分類だけ数えて、無料枠に収まるかの目安にする
            would_send += 1
            continue
        mail = gmail.get_message(service, message_id)
        try:
            if not dry_run and notion.mail_already_staged(mail.id):
                log.info("登録済みのためラベルのみ付与: %s", shown(mail.subject))
            else:
                time.sleep(max(0.0, last_call + settings.min_interval_seconds - time.monotonic()))
                last_call = time.monotonic()
                calls += 1
                result = extract(client, mail, settings)
                entries = build_entries(result, companies)
                log.info("%s → 就活関連=%s, %d 件", shown(mail.subject), result.is_job_related, len(entries))
                for entry in entries:
                    if dry_run:
                        log.info("  [dry-run] %s", shown(entry))
                        continue
                    notion.create_staged_entry(
                        title=entry.title, kind=entry.kind, start=entry.start, end=entry.end,
                        format_=entry.format, location=entry.location, todo=entry.todo,
                        company_page_id=entry.company_page_id,
                        extracted_company=entry.extracted_company, extracted_phase=entry.extracted_phase,
                        needs_review=entry.needs_review, note=entry.note,
                        mail_id=mail.id, mail_url=mail.url, body_blocks=body_excerpt(mail),
                    )
            if not dry_run:
                gmail.add_label(service, mail.id, label_id)
        except genai_errors.APIError as e:
            # Gemini のエラー本文にメールの内容は含まれないので、原因が分かるように表示する
            log.error("Gemini エラー(次回再試行): %s %s: %s", e.code, e.status, (e.message or "")[:300])
            if e.code == 429:
                log.warning("回数制限に達したため、今回の処理はここで終了します")
                stopped = True
                if not dry_run:
                    break
        except (ExtractionError, RuntimeError) as e:
            # ラベルを付けないので、次回の実行で再挑戦される
            log.error("処理失敗(次回再試行): %s: %s: %s", shown(mail.subject), type(e).__name__, shown(e))

    log.info("Gemini に送信: %d 件 / 広告として飛ばした: %d 件", calls, skipped)
    if dry_run and would_send:
        log.info("[dry-run] 残りで Gemini に送ることになるメール: %d 件", would_send)


def apply(dry_run: bool = False) -> None:
    companies = notion.list_companies()
    entries = notion.list_confirmed_entries()
    log.info("確認済エントリ: %d 件", len(entries))
    for entry in entries:
        company_id = entry["company_ids"][0] if entry["company_ids"] else None
        if company_id is None and entry["extracted_company"]:
            matched = company.find_match(entry["extracted_company"], companies)
            if matched:
                company_id = matched["id"]
            elif not dry_run:
                created = notion.create_company(entry["extracted_company"], entry["extracted_phase"] or None)
                companies.append(created)
                company_id = created["id"]
                log.info("企業ノートに新規作成: %s", shown(created["name"]))
        if dry_run:
            log.info("[dry-run] %s → 企業=%s, フェーズ=%s", shown(entry["title"]), company_id, entry["extracted_phase"])
            continue
        if company_id and not entry["company_ids"]:
            notion.link_company(entry["id"], company_id)
        if company_id and entry["extracted_phase"]:
            notion.update_company_phase(company_id, entry["extracted_phase"])
        if company_id is None:
            log.warning("企業名が無いため企業ノートには反映せず: %s", shown(entry["title"]))
        notion.mark_applied(entry["id"])
