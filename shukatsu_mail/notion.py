"""
notion.py
Notion API (data source ベース, Notion-Version 2025-09-03) を requests で直接叩く薄いラッパー。
shukatsu-mobile の notion_client.py と同じ書き方にそろえている。
"""

import requests

from .config import PHASES, STATUS_APPLIED, STATUS_CONFIRMED, STATUS_UNCONFIRMED, env

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"
TEXT_LIMIT = 2000  # rich_text 1要素あたりの上限

# 就活スケジュールに本ツールが追加する列(既存の列やビューには手を付けない)
P_STATUS = "ステータス"
P_REVIEW = "要確認"
P_NOTE = "確認メモ"
P_EXTRACTED_COMPANY = "抽出企業名"
P_EXTRACTED_PHASE = "抽出フェーズ"
P_MAIL_ID = "メールID"
P_MAIL_URL = "元メール"

ADDED_PROPERTIES = {
    P_STATUS: {"select": {"options": [
        {"name": STATUS_UNCONFIRMED, "color": "red"},
        {"name": STATUS_CONFIRMED, "color": "yellow"},
        {"name": STATUS_APPLIED, "color": "green"},
    ]}},
    P_REVIEW: {"checkbox": {}},
    P_NOTE: {"rich_text": {}},
    P_EXTRACTED_COMPANY: {"rich_text": {}},
    P_EXTRACTED_PHASE: {"select": {"options": [{"name": p} for p in PHASES]}},
    P_MAIL_ID: {"rich_text": {}},
    P_MAIL_URL: {"url": {}},
}


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {env('NOTION_TOKEN')}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _companies_ds() -> str:
    return env("NOTION_COMPANIES_DS")


def _schedule_ds() -> str:
    return env("NOTION_SCHEDULE_DS")


def _request(method: str, path: str, payload: dict | None = None) -> dict:
    resp = requests.request(method, f"{NOTION_API_BASE}{path}", headers=_headers(), json=payload, timeout=30)
    if not resp.ok:
        raise RuntimeError(f"Notion API {method} {path} -> {resp.status_code}: {resp.text[:500]}")
    return resp.json()


def _query_all(data_source_id: str, payload: dict | None = None) -> list[dict]:
    payload = dict(payload or {}, page_size=100)
    pages: list[dict] = []
    while True:
        data = _request("POST", f"/data_sources/{data_source_id}/query", payload)
        pages += data.get("results", [])
        if not data.get("has_more"):
            return pages
        payload["start_cursor"] = data["next_cursor"]


def _rich_text(value: str | None) -> list:
    return [{"type": "text", "text": {"content": (value or "")[:TEXT_LIMIT]}}]


def _plain_text(prop: dict) -> str:
    items = prop.get(prop.get("type"), []) or []
    return "".join(item.get("plain_text", "") for item in items)


def _select_name(prop: dict) -> str:
    return (prop.get("select") or {}).get("name", "")


# ---------------------------------------------------------------------------
# セットアップ
# ---------------------------------------------------------------------------

def ensure_schedule_properties() -> list[str]:
    """就活スケジュールに本ツール用の列が無ければ追加する。追加した列名を返す。"""
    ds = _request("GET", f"/data_sources/{_schedule_ds()}")
    missing = {name: spec for name, spec in ADDED_PROPERTIES.items() if name not in ds["properties"]}
    if missing:
        _request("PATCH", f"/data_sources/{_schedule_ds()}", {"properties": missing})
    return list(missing)


# ---------------------------------------------------------------------------
# 企業ノート
# ---------------------------------------------------------------------------

def list_companies() -> list[dict]:
    results = []
    for page in _query_all(_companies_ds()):
        props = page["properties"]
        results.append({
            "id": page["id"],
            "name": _plain_text(props.get("企業名", {})),
            "phase": _select_name(props.get("選考フェーズ", {})),
        })
    return results


def create_company(name: str, phase: str | None) -> dict:
    properties = {"企業名": {"title": _rich_text(name)}}
    if phase:
        properties["選考フェーズ"] = {"select": {"name": phase}}
    page = _request("POST", "/pages", {"parent": {"data_source_id": _companies_ds()}, "properties": properties})
    return {"id": page["id"], "name": name, "phase": phase or ""}


def update_company_phase(page_id: str, phase: str) -> None:
    _request("PATCH", f"/pages/{page_id}", {"properties": {"選考フェーズ": {"select": {"name": phase}}}})


# ---------------------------------------------------------------------------
# 就活スケジュール
# ---------------------------------------------------------------------------

def mail_already_staged(mail_id: str) -> bool:
    """ラベル付けに失敗した場合の二重登録を防ぐ。"""
    pages = _request("POST", f"/data_sources/{_schedule_ds()}/query", {
        "filter": {"property": P_MAIL_ID, "rich_text": {"equals": mail_id}},
        "page_size": 1,
    })
    return bool(pages.get("results"))


def create_staged_entry(
    title: str,
    kind: str,
    start: str | None,
    end: str | None,
    format_: str | None,
    location: str | None,
    todo: str | None,
    company_page_id: str | None,
    extracted_company: str | None,
    extracted_phase: str | None,
    needs_review: bool,
    note: str,
    mail_id: str,
    mail_url: str,
    body_blocks: list[str],
) -> dict:
    properties = {
        "件名": {"title": _rich_text(title)},
        "種別": {"select": {"name": kind}},
        "場所・URL": {"rich_text": _rich_text(location)},
        "事前タスク": {"rich_text": _rich_text(todo)},
        P_STATUS: {"select": {"name": STATUS_UNCONFIRMED}},
        P_REVIEW: {"checkbox": needs_review},
        P_NOTE: {"rich_text": _rich_text(note)},
        P_EXTRACTED_COMPANY: {"rich_text": _rich_text(extracted_company)},
        P_MAIL_ID: {"rich_text": _rich_text(mail_id)},
        P_MAIL_URL: {"url": mail_url},
    }
    if start:
        properties["日時"] = {"date": {"start": start, "end": end}}
    if format_:
        properties["形式"] = {"select": {"name": format_}}
    if company_page_id:
        properties["企業"] = {"relation": [{"id": company_page_id}]}
    if extracted_phase:
        properties[P_EXTRACTED_PHASE] = {"select": {"name": extracted_phase}}

    children = [
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _rich_text(text)}}
        for text in body_blocks
    ]
    return _request("POST", "/pages", {
        "parent": {"data_source_id": _schedule_ds()},
        "properties": properties,
        "children": children,
    })


def list_confirmed_entries() -> list[dict]:
    """本人が「確認済」にした、まだ企業ノートに反映していないエントリ。"""
    results = []
    for page in _query_all(_schedule_ds(), {
        "filter": {"property": P_STATUS, "select": {"equals": STATUS_CONFIRMED}},
        # 古いメール由来のものから反映し、最新の選考フェーズが最後に残るようにする
        "sorts": [{"timestamp": "created_time", "direction": "ascending"}],
    }):
        props = page["properties"]
        results.append({
            "id": page["id"],
            "title": _plain_text(props.get("件名", {})),
            "company_ids": [r["id"] for r in props.get("企業", {}).get("relation", [])],
            "extracted_company": _plain_text(props.get(P_EXTRACTED_COMPANY, {})),
            "extracted_phase": _select_name(props.get(P_EXTRACTED_PHASE, {})),
        })
    return results


def link_company(page_id: str, company_page_id: str) -> None:
    _request("PATCH", f"/pages/{page_id}", {"properties": {"企業": {"relation": [{"id": company_page_id}]}}})


def mark_applied(page_id: str) -> None:
    _request("PATCH", f"/pages/{page_id}", {"properties": {P_STATUS: {"select": {"name": STATUS_APPLIED}}}})
