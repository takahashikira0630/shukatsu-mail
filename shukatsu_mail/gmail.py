"""
gmail.py
リフレッシュトークンを使って、対話なしでGmailから就活関連の新着メールを取得する。
処理済みのメールにはラベルを付け、次回以降は検索から外す(状態をファイルに持たないため)。
初回のブラウザ認証は scripts/get_gmail_token.py で1回だけ行う。
"""

import base64
import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from .config import Settings, env

TOKEN_URI = "https://oauth2.googleapis.com/token"
# ラベル付けのため readonly ではなく modify が必要(メールの削除はできないスコープ)
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


@dataclass
class Mail:
    id: str
    subject: str
    sender: str
    received_at: datetime
    body: str
    # 一斉配信(メルマガ・広告)のヘッダーが付いているか
    bulk: bool = False

    @property
    def url(self) -> str:
        return f"https://mail.google.com/mail/u/0/#all/{self.id}"


def build_service():
    creds = Credentials(
        token=None,
        refresh_token=env("GMAIL_REFRESH_TOKEN"),
        token_uri=TOKEN_URI,
        client_id=env("GOOGLE_CLIENT_ID"),
        client_secret=env("GOOGLE_CLIENT_SECRET"),
        scopes=SCOPES,
    )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def build_query(settings: Settings) -> str:
    """送信元ドメイン OR 件名キーワードのどれかに当たる、未処理の新着メールを探す検索式。"""
    terms = [f"from:{d}" for d in settings.sender_domains]
    terms += [f'subject:"{k}"' for k in settings.subject_keywords]
    parts = [
        f"newer_than:{settings.newer_than_days}d",
        f'-label:"{settings.processed_label}"',
        f'-label:"{settings.skipped_label}"',
        "{" + " ".join(terms) + "}",
    ]
    if settings.extra_query:
        parts.append(settings.extra_query)
    return " ".join(parts)


def list_new_message_ids(service, settings: Settings) -> list[str]:
    query = build_query(settings)
    ids: list[str] = []
    page_token = None
    while True:
        resp = service.users().messages().list(
            userId="me", q=query, pageToken=page_token, maxResults=100
        ).execute()
        ids += [m["id"] for m in resp.get("messages", [])]
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    # 古い順に処理する(同じ企業の連絡が続いたとき、後のメールの内容が最後に残るように)
    return list(reversed(ids))


def get_message(service, message_id: str) -> Mail:
    msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
    if "date" in headers:
        received_at = parsedate_to_datetime(headers["date"])
    else:
        received_at = datetime.fromtimestamp(int(msg["internalDate"]) / 1000, tz=timezone.utc)
    return Mail(
        id=message_id,
        subject=headers.get("subject", ""),
        sender=headers.get("from", ""),
        received_at=received_at,
        body=extract_body(msg["payload"]),
        bulk="list-unsubscribe" in headers or headers.get("precedence", "").lower() in ("bulk", "list"),
    )


def extract_body(payload: dict) -> str:
    """text/plain を優先し、無ければ text/html をテキスト化して返す。"""
    plain = _find_part(payload, "text/plain")
    if plain:
        return plain
    rich = _find_part(payload, "text/html")
    return html_to_text(rich) if rich else ""


def _find_part(payload: dict, mime_type: str) -> str:
    if payload.get("mimeType") == mime_type and payload.get("body", {}).get("data"):
        return _decode(payload["body"]["data"])
    for part in payload.get("parts", []) or []:
        found = _find_part(part, mime_type)
        if found:
            return found
    return ""


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def html_to_text(source: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", source)
    # リンク先URLは面接会場やWebテストの受検URLであることが多いので残す
    text = re.sub(r'(?is)<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r"\2 (\1)", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t　]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def ensure_label(service, name: str) -> str:
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    for label in labels:
        if label["name"] == name:
            return label["id"]
    created = service.users().labels().create(
        userId="me",
        body={"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"},
    ).execute()
    return created["id"]


def add_label(service, message_id: str, label_id: str) -> None:
    service.users().messages().modify(
        userId="me", id=message_id, body={"addLabelIds": [label_id]}
    ).execute()
