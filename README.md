# 高校生向け学習掲示板

Python/Flaskで作成したローカル実行可能な学習掲示板です。

## ローカル起動

```powershell
cd C:\HAL\SK21\AzureKadai
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

ブラウザで http://127.0.0.1:5051 を開きます。PORT環境変数で別のポートを指定できます。

## デモアカウント

- メールアドレス: `demo@example.com`
- パスワード: `demo1234`

初回起動時にSQLiteデータベースとデモデータを `instance/study_board.sqlite3` に作成します。

## Azure向けの配置方針

シークレットはソースへ保存せず、Azure App Serviceのアプリケーション設定へ設定します。

- Blob接続文字列などの設定名: 現在は未実装です。デプロイ時に環境変数へ追加し、画像アップロード実装へ接続します。
- Azure Tables: 必要に応じてPythonのデータ層へアダプターを追加します。
- SQLite: App Serviceの永続性を考慮し、本番運用時はAzure SQL等へ移行するか、永続ストレージ方式を選定します。
