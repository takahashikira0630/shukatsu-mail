# プライバシーポリシー

shukatsu-mail は、開発者本人が自分の就職活動のためだけに使う個人用ツールです。第三者へのサービス提供は行っていません。

## 取得する情報

- 本人の Gmail のうち、`config.toml` の条件(就活サイトの送信元ドメイン・件名キーワード)に一致するメールの件名・差出人・受信日時・本文
- Gmail のラベル情報(処理済みのメールにラベルを付けるため)

Google API の権限は `gmail.modify` のみを使い、メールの送信・削除は行いません。

## 利用目的と送信先

取得したメールは、次の目的にだけ使います。

1. Google の Gemini API に送り、企業名・選考フェーズ・日時・必要な対応を抽出する
2. 抽出結果とメール本文の冒頭を、本人の Notion ワークスペースに書き込む

上記以外の第三者に情報を提供・販売することはありません。本ツール自体はデータベースやログにメールの内容を保存しません(GitHub Actions のログでは件名・企業名を伏せています)。

## Google API から取得した情報の扱い

本ツールによる Google API から取得した情報の利用は、[Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy)(Limited Use の要件を含む)に従います。

## アクセスの取り消し

https://myaccount.google.com/permissions から、いつでも本ツールのアクセス権を取り消せます。

## 問い合わせ

https://github.com/takahashikira0630/shukatsu-mail/issues

最終更新: 2026-09-26
