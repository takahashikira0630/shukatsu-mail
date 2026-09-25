"""
get_gmail_token.py

【使い方】
1. Google Cloud Console で Gmail API を有効にし、OAuthクライアント(デスクトップアプリ)の
   credentials.json を、このスクリプトと同じフォルダに置く
   (shukatsu-mobile で作った credentials.json を流用してよい。Gmail API の有効化だけ追加する)
2. ターミナルで `python get_gmail_token.py` を実行する
3. ブラウザが開くので、就活メールが届くGoogleアカウントでログイン・許可する
4. 表示される3つの値を .env と GitHub Secrets に設定する

最初の1回だけ実行すればよい。以降はこのリフレッシュトークンでアクセストークンを自動更新する。
"""

from google_auth_oauthlib.flow import InstalledAppFlow

# ラベル付けのため modify(既読・ラベル操作ができ、削除はできない)
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def main() -> None:
    flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
    creds = flow.run_local_server(port=0)

    print("\n以下の値を .env と GitHub Secrets に設定してください。\n")
    print(f"GOOGLE_CLIENT_ID={creds.client_id}")
    print(f"GOOGLE_CLIENT_SECRET={creds.client_secret}")
    print(f"GMAIL_REFRESH_TOKEN={creds.refresh_token}")


if __name__ == "__main__":
    main()
