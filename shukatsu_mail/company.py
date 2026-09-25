"""
company.py
抽出した企業名を、企業ノートの既存レコードと突き合わせる。
完全一致だと「株式会社」の有無や全角/半角の違いで外れるので、表記を正規化してから比べる。
略称(例: 〇〇ホールディングス → 〇〇HD)までは推測しない。外れたら「要確認」で本人に任せる。
"""

import re
import unicodedata

# 法人格の表記(前株・後株の両方、略記を含む)
_CORPORATE_FORMS = [
    "株式会社", "有限会社", "合同会社", "合資会社", "合名会社",
    "一般社団法人", "一般財団法人", "公益社団法人", "公益財団法人",
    "(株)", "(有)", "(同)", "㈱", "㈲",
]
_SUFFIX_EN = re.compile(r"(,?\s*(inc|co|corp|ltd|llc|co\.?,?\s*ltd)\.?)+$", re.IGNORECASE)
_SPACES = re.compile(r"[\s・･.,、。]+")


def normalize(name: str) -> str:
    # NFKC で全角英数・半角カナ・㈱ などをそろえる(㈱ → (株))
    text = unicodedata.normalize("NFKC", name or "").strip()
    for form in _CORPORATE_FORMS:
        text = text.replace(unicodedata.normalize("NFKC", form), "")
    text = _SUFFIX_EN.sub("", text.strip())
    text = _SPACES.sub("", text)
    return text.lower()


def find_match(name: str, companies: list[dict]) -> dict | None:
    """companies は [{id, name, ...}]。正規化後に一致する企業が1社だけなら返す。"""
    key = normalize(name)
    if not key:
        return None
    hits = [c for c in companies if normalize(c["name"]) == key]
    return hits[0] if len(hits) == 1 else None
