# 外来服薬支援 v0.15.13 検証記録

## 患者フォルダ方式

- 患者ごとの保存先を `work_dir/medi_sup/患者名/` に統一した。
- 最新の内部記録は常に `latest.json`。
- 上書き前のJSONは同じ患者フォルダの `_backup/` へ日時名で退避する。
- 90mm PDFは `print/`、ODTは患者フォルダ直下の `外来服薬支援.odt` へ保存する。
- 画面の「前回記録を引継ぐ」では患者フォルダを選び、内部で `latest.json` を開く。
- 初回に `latest.json` がなく旧ODTが1件だけある場合、そのODTを自動で引き継ぐ。
- 旧ODTが複数ある場合は、対象のODTを選択する画面を表示する。
- ODTから患者名を読み取り、患者フォルダ、`外来服薬支援.odt`、`latest.json` を作成する。
- 元のODTは移動・削除せず、そのまま保持する。
- `latest.json` も旧ODTもない場合は、意味の分かるエラーを表示する。
- 旧形式のJSON・ODTをファイルパスで渡す既存の読込処理は維持する。
- 履歴画面は新方式の最新・バックアップと、旧方式のファイルを両方読み取り専用で表示する。

## 検証

患者フォルダへのJSON・ODT保存、フォルダ指定からの再読込、上書き履歴、JSON/ODT再現性を実際のTk画面部品で確認した。

```powershell
& '.\bill.venv311\Scripts\python.exe' -m unittest discover -s tests -p 'test_medi_sup*.py'
```

115件成功。
