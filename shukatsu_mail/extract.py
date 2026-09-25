"""
extract.py
メール本文を Claude API に渡し、企業名・選考フェーズ・日時・必要アクションを構造化して取り出す。
スキーマは structured outputs で強制し、日付の妥当性などはコード側でもう一度確かめる。
"""

from datetime import date, datetime
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from .config import JST, Settings
from .gmail import Mail

Phase = Literal["エントリー", "書類選考", "一次面接", "二次面接", "三次面接", "最終面接", "内定", "不合格", "辞退"]
Kind = Literal["選考", "インターン", "説明会", "その他"]
Format = Literal["対面", "オンライン", "その他"]


class ExtractedEvent(BaseModel):
    title: str = Field(description="予定・締切の短い名前。例: 一次面接 / ES提出締切 / 適性検査受検期限")
    kind: Kind
    start: str | None = Field(description="開始日時または締切日時。日本時間で YYYY-MM-DD か YYYY-MM-DDTHH:MM")
    end: str | None = Field(description="終了日時(範囲がある場合のみ)。形式は start と同じ")
    is_deadline: bool = Field(description="提出・受検・返信などの期限なら true、面接や説明会の開催日時なら false")
    format: Format | None
    location_or_url: str | None = Field(description="会場の住所、またはWeb会議・受検・提出のURL")
    todo: str | None = Field(description="本人がやるべきこと。例: 日程調整フォームに回答 / ESを提出")
    date_uncertain: bool = Field(description="日付の年や時刻を推測で補った、または複数の候補日から選べない場合 true")


class Extraction(BaseModel):
    is_job_related: bool = Field(description="本人の就職活動の選考・イベントに関する連絡なら true。広告・一般的なメルマガは false")
    company_name: str | None = Field(description="選考を行う企業の正式名称(就活サイト名ではない)")
    via_service: str | None = Field(description="経由した就活サイト・エージェント名(直接の連絡なら null)")
    phase: Phase | None = Field(description="このメールから分かる現在の選考フェーズ。変化が読み取れなければ null")
    events: list[ExtractedEvent]
    needs_review: bool = Field(description="企業名の表記が曖昧、日付が曖昧など、本人の確認が必要なら true")
    review_reasons: list[str] = Field(description="needs_review の理由(日本語で短く)")


SYSTEM_PROMPT = """\
あなたは就職活動中の大学生のメールを整理するアシスタントです。
メール1通から、Notionの選考管理データベースに登録する情報を取り出してください。

- company_name は選考を行う企業名です。就活サイト(マイナビ、OfferBox など)の名前を入れないでください。
  メールから確実に読み取れる表記をそのまま使い、略称を正式名称に推測で置き換えないでください。
- phase は「書類選考通過・一次面接のご案内」なら 一次面接 のように、次に受ける段階を入れます。
  不合格の通知なら 不合格、内定の通知なら 内定 です。フェーズが変わらない連絡なら null です。
- events には日時の決まった予定と、期限のあるタスクをすべて入れます。候補日から選ぶ日程調整は、
  回答期限をイベントにし(無ければ start を null)、候補日は todo に書きます。
- 年が書かれていない日付は、受信日時より後で最も近い日付として補い、date_uncertain を true にします。
- 抽出に自信が持てない点があれば needs_review を true にし、理由を review_reasons に書きます。
  見落としのほうが誤検知より困るので、迷ったら確認を求めてください。
- 広告・メルマガ・就活サイトの一般的なお知らせは is_job_related を false にし、events を空にします。
"""


def build_user_message(mail: Mail, body: str) -> str:
    return (
        f"受信日時: {mail.received_at.astimezone(JST).strftime('%Y-%m-%d %H:%M (%a)')}\n"
        f"差出人: {mail.sender}\n"
        f"件名: {mail.subject}\n\n"
        f"本文:\n{body}"
    )


class ExtractionError(Exception):
    pass


def extract(client: anthropic.Anthropic, mail: Mail, settings: Settings) -> Extraction:
    body = mail.body
    truncated = len(body) > settings.max_body_chars
    if truncated:
        body = body[: settings.max_body_chars]

    response = client.beta.messages.parse(
        model=settings.model,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_user_message(mail, body)}],
        output_format=Extraction,
        # 安全性フィルタで断られたときは、サーバー側で別モデルに切り替えて再実行する
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise ExtractionError(f"抽出できませんでした(stop_reason={response.stop_reason})")

    result = response.parsed_output
    if truncated:
        result.needs_review = True
        result.review_reasons.append(f"本文が長いため先頭{settings.max_body_chars}文字だけで抽出")
    validate_dates(result)
    return result


def parse_local_datetime(value: str) -> date | datetime:
    """'YYYY-MM-DD' は date、'YYYY-MM-DDTHH:MM' は datetime として読む。形式違いは ValueError。"""
    if "T" in value:
        return datetime.strptime(value[:16], "%Y-%m-%dT%H:%M")
    return date.fromisoformat(value)


def validate_dates(result: Extraction) -> None:
    for event in result.events:
        for label, value in (("開始", event.start), ("終了", event.end)):
            if value is None:
                continue
            try:
                parse_local_datetime(value)
            except ValueError:
                result.needs_review = True
                result.review_reasons.append(f"「{event.title}」の{label}日時を読めませんでした: {value}")
                if label == "開始":
                    event.start = None
                else:
                    event.end = None
        if event.date_uncertain:
            result.needs_review = True
            result.review_reasons.append(f"「{event.title}」の日付は推測を含みます")
        if event.is_deadline is False and event.start is None:
            result.needs_review = True
            result.review_reasons.append(f"「{event.title}」の日時が見つかりません")
