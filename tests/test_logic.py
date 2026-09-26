"""ネットワークを使わない部分のテスト。python -m unittest discover tests で実行する。"""

import unittest
from pathlib import Path

from shukatsu_mail import company
from shukatsu_mail.__main__ import load_mail_file
from shukatsu_mail.config import Settings
from shukatsu_mail.extract import Extraction, ExtractedEvent, validate_dates
from shukatsu_mail.gmail import build_query, html_to_text
from shukatsu_mail.gmail import Mail
from shukatsu_mail.pipeline import build_entries, is_probably_ad, to_notion_date

SAMPLES = Path(__file__).parent / "samples"
COMPANIES = [
    {"id": "c1", "name": "架空テック株式会社", "phase": "書類選考"},
    {"id": "c2", "name": "(株)サンプル商事", "phase": "エントリー"},
    {"id": "c3", "name": "Example Inc.", "phase": ""},
]


def event(**kw) -> ExtractedEvent:
    base = dict(title="一次面接", kind="選考", start="2026-10-02T14:00", end="2026-10-02T15:00",
                is_deadline=False, format="オンライン", location_or_url="https://zoom.example/j/1",
                todo=None, date_uncertain=False)
    return ExtractedEvent(**(base | kw))


def extraction(**kw) -> Extraction:
    base = dict(is_job_related=True, company_name="架空テック株式会社", via_service=None, phase="一次面接",
                events=[event()], needs_review=False, review_reasons=[])
    return Extraction(**(base | kw))


class CompanyMatchTest(unittest.TestCase):
    def test_corporate_form_variants(self):
        for name in ["架空テック", "架空テック株式会社", "株式会社 架空テック", "架空テック㈱", "架空テック(株)"]:
            self.assertEqual(company.find_match(name, COMPANIES)["id"], "c1", name)

    def test_fullwidth_and_english_suffix(self):
        self.assertEqual(company.find_match("サンプル商事", COMPANIES)["id"], "c2")
        self.assertEqual(company.find_match("EXAMPLE, Inc.", COMPANIES)["id"], "c3")
        self.assertEqual(company.find_match("Ｅｘａｍｐｌｅ", COMPANIES)["id"], "c3")

    def test_no_guess_for_abbreviation(self):
        self.assertIsNone(company.find_match("架空", COMPANIES))
        self.assertIsNone(company.find_match("", COMPANIES))


class BuildEntriesTest(unittest.TestCase):
    def test_matched_company(self):
        [entry] = build_entries(extraction(), COMPANIES)
        self.assertEqual(entry.company_page_id, "c1")
        self.assertEqual(entry.title, "架空テック株式会社|一次面接")
        self.assertEqual(entry.start, "2026-10-02T14:00:00+09:00")
        self.assertFalse(entry.needs_review)

    def test_unknown_company_needs_review(self):
        [entry] = build_entries(extraction(company_name="新規株式会社"), COMPANIES)
        self.assertIsNone(entry.company_page_id)
        self.assertTrue(entry.needs_review)
        self.assertIn("見つかりません", entry.note)

    def test_phase_only_mail(self):
        [entry] = build_entries(extraction(events=[], phase="不合格"), COMPANIES)
        self.assertEqual(entry.extracted_phase, "不合格")
        self.assertIsNone(entry.start)

    def test_not_job_related(self):
        self.assertEqual(build_entries(extraction(is_job_related=False), COMPANIES), [])
        self.assertEqual(build_entries(extraction(events=[], phase=None), COMPANIES), [])


class DateTest(unittest.TestCase):
    def test_to_notion_date(self):
        self.assertEqual(to_notion_date("2026-10-05"), "2026-10-05")
        self.assertEqual(to_notion_date("2026-10-05T23:59"), "2026-10-05T23:59:00+09:00")
        self.assertIsNone(to_notion_date(None))

    def test_invalid_date_is_dropped_and_flagged(self):
        result = extraction(events=[event(start="10月2日", end=None)])
        validate_dates(result)
        self.assertIsNone(result.events[0].start)
        self.assertTrue(result.needs_review)

    def test_uncertain_date_flagged(self):
        result = extraction(events=[event(date_uncertain=True)])
        validate_dates(result)
        self.assertTrue(result.needs_review)


class GmailHelpersTest(unittest.TestCase):
    def test_query(self):
        s = Settings(newer_than_days=3, processed_label="p", skipped_label="s", important_keywords=[],
                     sender_domains=["mynavi.jp"], subject_keywords=["面接"], extra_query="",
                     max_body_chars=30000, model="m", max_mails_per_run=5, min_interval_seconds=13)
        self.assertEqual(build_query(s), 'newer_than:3d -label:"p" -label:"s" {from:mynavi.jp subject:"面接"}')

    def test_ad_filter(self):
        def mail(subject, bulk):
            return Mail(id="x", subject=subject, sender="", received_at=None, body="", bulk=bulk)
        keywords = ["面接", "締切"]
        self.assertTrue(is_probably_ad(mail("【今週の特集】人気企業まとめ", True), keywords))
        self.assertFalse(is_probably_ad(mail("一次面接のご案内", True), keywords))      # 一斉配信でも重要語があれば送る
        self.assertFalse(is_probably_ad(mail("今後のご案内", False), keywords))         # 個別メールは必ず送る

    def test_html_keeps_links(self):
        text = html_to_text('<p>受検は<a href="https://t.example/x">こちら</a></p><style>p{}</style>')
        self.assertEqual(text, "受検はこちら (https://t.example/x)")

    def test_load_sample(self):
        mail = load_mail_file(SAMPLES / "01_interview.txt")
        self.assertIn("一次面接", mail.subject)
        self.assertEqual(mail.received_at.strftime("%Y-%m-%d %H:%M"), "2026-09-24 10:15")
        self.assertTrue(mail.body.startswith("山田 太郎 様"))


if __name__ == "__main__":
    unittest.main()
