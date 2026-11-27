# 高校生向け学習掲示板

Python/Flask製の学習掲示板です。ユーザー、問題、解答、コメント、学習活動、目標、グループをAzure Table Storageへ保存し、問題画像をAzure Blob Storageへ保存します。SQLiteは使用しません。

## ローカル起動

ローカル実行時もAzure Storage Accountが必要です。次のいずれかで認証してください。

### 推奨: Azure CLIの資格情報

Azure CLIでサインインし、サインインしたユーザーへStorage Accountの `Storage Table Data Contributor` と `Storage Blob Data Contributor` ロールを割り当てます。

```powershell
az login
$env:AZURE_TABLES_ACCOUNT_URL = "https://<Storage Account名>.table.core.windows.net"
$env:AZURE_STORAGE_ACCOUNT_URL = "https://<Storage Account名>.blob.core.windows.net"
$env:AZURE_STORAGE_CONTAINER = "problem-images"
```

必要な場合だけ、初期データをAzure Storageへ投入します。

```powershell
$env:SEED_DEMO_DATA = "true"
```

ローカルの開発環境でAzure CLIの資格情報を使い、アプリを起動します。

```powershell
cd C:\HAL\SK21\AzureKadai
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python app.py
```

ブラウザで http://127.0.0.1:5051 を開きます。ポートは `PORT` 環境変数で変更できます。

`.env`に`AZURE_STORAGE_CONNECTION_STRING`を設定すると、アプリがローカル開発時に読み込みます。`.env`はGitへコミットせず、接続文字列を画面共有やチャットへ貼らないでください。App Service上では`.env`ではなく、アプリケーション設定かマネージドIDを使います。

## デモアカウント

初回起動時に `SEED_DEMO_DATA=true` を明示した場合、デモユーザーとサンプルデータがAzure Table Storageに作成されます。既定値は`false`です。

- メールアドレス: `demo@example.com`
- パスワード: `demo1234`

このデモアカウントは公開環境で利用しないでください。App Serviceでは既定でデモデータを投入しません。

## テスト

```powershell
python -m pytest -q
```

自動テストはテスト専用のインメモリストアを使い、Azureリソースへデータを書き込みません。

## Azure App Serviceへのデプロイ

Azureポータルで行う設定とデプロイ手順は [AZURE_DEPLOYMENT.md](./AZURE_DEPLOYMENT.md) を参照してください。

Azure Table Storageがユーザー・掲示板データを保持し、Azure Blob Storageが画像を保持します。両方ともApp ServiceのマネージドIDで接続する構成です。
