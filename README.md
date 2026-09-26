# 就活メール自動化ツール

Gmail に届いた就活メールを Gemini API で読み取り、Notion の「就活スケジュール」に**未確認**の状態で追加するツール。
新しい画面は作らない。いつも通り Notion を見て、内容を確かめて「確認済」にするだけで企業ノートに反映される。

## 流れ

```
GitHub Actions(日本時間8〜23時台に15分ごと)
  └ stage: Gmail の新着就活メール → Gemini で抽出 → 就活スケジュールに「未確認」で追加
           → メールに shukatsu-mail/processed ラベル(次回から対象外)
  └ apply: 本人が「確認済」にした行 → 企業ノートに紐付け・選考フェーズ更新 → 「反映済」
```

- 1通のメールに予定が複数あれば、1予定=1行で追加する。予定がなく「書類選考通過」のようにフェーズだけ変わる連絡は、日時なしの1行になる
- 自信のない抽出(日付の年を補った、企業ノートに無い企業名など)は「要確認」にチェックが入り、理由が「確認メモ」に書かれる
- 各行のページ本文にメールの件名・差出人・本文の冒頭が入り、「元メール」から Gmail を開ける

### 企業名の紐付け

企業ノートの「企業名」と、表記をそろえてから比べる(`株式会社`・`(株)`・`㈱` の有無、全角/半角、`Inc.` などを無視)。
略称や別名までは推測しない。一致しなければ「企業」列は空のまま「要確認」になるので、

- 既存企業なら、Notion で「企業」列を手で紐付けてから「確認済」にする
- 新しい企業なら、そのまま「確認済」にすると企業ノートに新規作成される

## セットアップ

### 1. Notion

1. https://www.notion.so/profile/integrations でインテグレーションを作り、トークンを控える(shukatsu-mobile のものを流用してもよい)
2. 「就活オールインワン・ポータル」ページの「…」→「コネクトの追加」で接続する
3. 本ツール用の列は、初回の実行時に就活スケジュールへ自動で追加される(既存の列・ビューは変更しない)。手元で先に追加したいときは `python -m shukatsu_mail setup-notion`

   追加される列: ステータス(未確認/確認済/反映済)・要確認・確認メモ・抽出企業名・抽出フェーズ・メールID・元メール
4. Notion で就活スケジュールに「未確認」ビューを作る(フィルタ: ステータス = 未確認、並び順: 日時)。スマホではこのビューを開けば確認待ちが一覧できる

### 2. Gmail(最初の1回だけ)

1. Google Cloud Console で **Gmail API** を有効にする(shukatsu-mobile で作ったプロジェクトと credentials.json を流用できる)
2. `credentials.json` を `scripts/` に置いて実行する

   ```bash
   cd scripts
   python get_gmail_token.py
   ```

3. 就活メールが届くアカウントでログインし、表示された `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GMAIL_REFRESH_TOKEN` を控える

OAuth 同意画面が「テスト」のままだと、リフレッシュトークンは7日で失効する。「本番」に切り替えておく(個人利用なら審査なしで使える)。

### 3. Gemini API

https://aistudio.google.com/apikey で API キーを発行する(シフト表アプリと同じキーでもよい)。

### 4. ローカルで試す

```bash
cp .env.example .env   # 値を書き込む
pip install -r requirements.txt
python -m unittest discover tests                 # ネットワークを使わないテスト
python -m shukatsu_mail extract tests/samples/*.txt  # 架空メールで抽出だけ試す(Gmail・Notionに触れない)
python -m shukatsu_mail run --dry-run             # 実際のGmailを読むが、Notion・ラベルには書かない
python -m shukatsu_mail run                       # 本番
```

`extract` は `.eml`(Gmail の「メッセージのソースを表示」→ダウンロード)も読める。実際のメールは `samples_private/` に置けば git に入らない。

### 5. GitHub Actions

1. このフォルダを GitHub リポジトリに push する(`.env` と `credentials.json` は `.gitignore` 済み)
2. Settings → Secrets and variables → Actions に、`.env.example` の7つの値を登録する
3. Actions タブで `sync-shukatsu-mail` を選び「Run workflow」で手動実行する。「Notion・Gmailに書き込まずに試す」にチェックが入った状態(既定)で、まず接続と件数を確認する

## 注意点

- **実行間隔**: 日本時間の8:00〜23:45に15分ごと。夜中に届いたメールは朝8時の実行で取り込まれる。公開リポジトリなので Actions の実行時間に上限はない。非公開にする場合は無料枠(月2,000分、1回=1分で数える)に収まるよう、この間隔(約1,800分/月)より増やさないこと
- **公開リポジトリのスケジュール実行は、60日間コミットが無いと自動で止まる**。止まったら Actions タブから再有効化する
- 認証情報はすべて GitHub Secrets に置き、コードや `config.toml` には書かない。Actions のログにもメール本文は出さず、件名だけを出す
- GitHub の cron は混雑時に数分〜十数分遅れることがある
- 処理に失敗したメールにはラベルを付けないので、次回の実行で再挑戦される(`newer_than_days` の日数を過ぎると対象外になる)
- 対象メールの絞り込みは `config.toml` の送信元ドメインと件名キーワード。拾い漏れがあればここに追記する
- **Gemini の無料枠とメールの内容**: 無料枠では、送った内容(メール本文=企業名・選考状況)が Google のサービス改善に使われることがある。気になる場合は Google AI Studio で課金を有効にする(有料枠のデータは改善に使われない)
- 無料枠には1分・1日あたりの回数制限がある。制限に当たったメールはラベルが付かず、次回の実行で再挑戦される
