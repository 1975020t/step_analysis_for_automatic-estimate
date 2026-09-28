# 資料一覧（板金見積システム）

STEP・展開図DXF・図面PDFから板金部品の見積を作り、見積書を出し、過去の類似見積を参照するデモについての資料です。完成品は **PDF**（`docs/pdf/`）で、元の原稿（Markdown）と画像もこのフォルダにあります。

| 資料 | PDF | 読む人 | 中身 |
|---|---|---|---|
| 要件定義書 | [pdf/01_要件定義書.pdf](pdf/01_要件定義書.pdf) | 経営者・顧客・開発者 | 何のためのシステムで、何ができるべきか。業務の流れ、機能要件、非機能要件（精度・時間・LLMの方針）、今後の課題、用語集 |
| 設計書 | [pdf/02_設計書.pdf](pdf/02_設計書.pdf) | 開発者 | どう作っているか。解析・読み取り・見積・見積書・類似見積・API の実装ロジック（計算式・判定条件・しきい値）、データ設計、評価の仕組み |
| 画面設計書 | [pdf/03_画面設計書.pdf](pdf/03_画面設計書.pdf) | 全員 | 画面ごとの項目・操作・表示（実際の画面の写真）、見積書の見本 |
| 操作マニュアル | [pdf/04_操作マニュアル.pdf](pdf/04_操作マニュアル.pdf) | 見積担当者・導入担当者 | セットアップ（Windows）、日々の操作（写真つき）、表示の意味、よくある質問、API の使い方 |
| 評価報告書 | [pdf/05_評価報告書.pdf](pdf/05_評価報告書.pdf) | 経営者・顧客・開発者 | 解析と図面PDFからの情報抽出を、どう評価し、どんなスコアだったか（グラフつき）。まだ確かめられていないこと |

## どれを読めばよいか

- **まず全体を知りたい**：要件定義書 →（数字が知りたければ）評価報告書
- **見積を作る担当者**：操作マニュアルの「日々の操作」→ 画面設計書
- **導入する人**：操作マニュアルの「セットアップ」
- **開発する人**：設計書 → 画面設計書 → 評価報告書（`analysis/api_design.md` も）

## 作り直し方

原稿は `docs/0N_*.md`、画像は `docs/images/`（画面の写真 `screens/`、グラフ `charts/`、図 `diagrams/`）。

```
pip install -r docs/requirements.txt                 # markdown
python docs/scripts/capture_screens.py               # 画面の写真（デモを起動して撮影。LLM は呼ばない）
python docs/scripts/make_charts.py                   # 評価のグラフ（数字は analysis/ のレポートの表から読む）
python docs/scripts/build_pdf.py                     # PDF（docs/pdf/）
```

- 図（構成図・流れ図・シーケンス図・状態遷移図）は原稿の中に Mermaid で書き、`build_pdf.py` が SVG にして PDF に描き込む。描いた図は `docs/images/diagrams/` に保存され、図を変えないかぎり Node.js なしで作り直せる。図を変えたときは一度 `cd docs/scripts && npm install` を行う（mermaid と、API 仕様書の撮影用の swagger-ui-dist）
- 画面の写真は、`docs/scripts/demo_app.py`（本物の画面に、記録済みの図面の読み取り結果 `recorded_reading.json` を渡すもの）と API を一時的な保存先で起動して撮る。コミットされた見積履歴は変わらない
- Playwright と Chromium、pypdfium2 が必要（開発環境に入っているもの）。日本語フォントはリポジトリの IPAゴシック（`fonts/ipag.ttf`）を埋め込む
