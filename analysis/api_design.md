# API の設計

2026-09-28（段階1：サーバー部分の切り出し、[handoff_api.md](handoff_api.md)）、2026-10-05（画面とデータベース、[handoff_ui.md](handoff_ui.md)）。仕様書は API を起動して `http://localhost:8000/docs`（OpenAPI は `/openapi.json`）で見られる。

## 構成

```
React の画面（web/、Vite。npm run build で web/dist）
        │ HTTP（JSON）。docker compose では nginx が /api を API に中継。Docker なしでは API が web/dist も配信
        ▼
  api/main.py（FastAPI）・api/platform_routes.py（画面用）・api/security.py（合言葉と操作者）
   入出力の型・認証・制限・エラー
        ▼
  src/services/platform/（画面の業務。データベースを使う）
   pricing.py   見積の条件（QuoteInputs）・未入力の項目・金額（QuoteEngine と price_summary）
   quotes.py    見積の作成（受付番号で段階を進める）・読み出し・変更・チャット・修正履歴
   drawings.py  図面の登録と版・登録時の解析と読み取り・検索・プレビュー・書き込み
   documents.py 見積書・納品書・請求書の発行とプレビュー（未入力があれば 409）
   cases.py     案件・進捗・警告・ステータス設定・振り返り
   similar.py   類似の図面と実績（src/similar_quotes.is_similar）
   library.py   書類と検索、admin.py 設定とマスター編集と見積ロジック、masters.py DB のマスター
   core.py      Platform（DB・マスターと履歴の読み込み直し・受付）
        ▼
  src/services/（処理の層。Web の枠組みに依存しない）
   estimate.py  EstimateService：解析・図面の読み取り・見積・見積書・類似見積・履歴・3D表示（今までの API もこれを呼ぶ）
   jobs.py      受付（状態をファイルに保存、スレッドで実行、途中の進み具合）
   storage.py   ファイルの置き場（LocalFileStore）、settings.py 置き場所と制限、pdfium_lock.py
        ▼
  src/db/（SQLAlchemy のモデル・接続・初期データ）、migrations/（Alembic）
  既存の処理：sheetmetal_analyzer・dxf_analyzer・pdf_reader・pdf_quote・quote_engine・quote_document・
            quote_pdf・quote_log・past_quotes・similar_quotes（解析と金額の計算は変えていない）
```

- **処理を2か所に書かない**：画面は API だけを呼ぶ。金額（費目ごとの小計を含む）、未入力の項目、発行できるか（帳票の種類ごとの理由）、類似かどうかは API が返し、画面は表示するだけ。画面・API・帳票PDFで金額が一致することをテストで確かめている（`tests/test_platform.py`、`tests/test_e2e_flow.py`）
- **解析と金額の計算は変えていない**：変えたのは見積の条件の扱い（未登録の単価・解析不可のときの形状の値の入力、発行できる条件、希望納期）と類似の条件だけ。入力した単価・形状の値でも `QuoteEngine` がルールで計算する
- **今までの API**（`/api/quotes`・`/api/documents`・`/api/similar-quotes`・`/api/history`）も動く。マスターと履歴はデータベースのものを使う

## 画面用のエンドポイント

| 機能 | メソッドとパス | 説明 |
|---|---|---|
| 選択肢 | `GET /api/meta`、`GET /api/recent` | 材質・表面処理・追加加工・担当者・ステータス・分類・属性・テンプレート・読み取りの可否／最近の見積 |
| 図面 | `POST /api/drawings/register`（202） | 1件＝図面PDF・形状ファイル（片方でもよい）と図番・品名・顧客・改訂・分類・版の指定。形状解析・PDFの文字・読み取り結果の保存は受付番号（register）で |
| | `GET /api/drawings`（条件はクエリ。自社の属性は `attrs` に JSON）、`GET /api/drawings/same-number`、`GET/PUT /api/drawings/{id}`、`PUT /api/drawings/{id}/current-revision`、`GET /api/drawings/{id}/similar`、`POST /api/drawings/{id}/memos`、`POST /api/revisions/{id}/notes`、`DELETE /api/notes/{id}`、`GET /api/revisions/{id}/preview.png`、`GET /api/files/{id}/raw` | |
| 見積 | `POST /api/estimates`（202） | 案件と見積を作り、受付番号（estimate）で 形状解析 → 図面の読み取り → マスタ照合 → 見積計算 → 類似実績の検索。`GET /api/jobs/{id}` の `progress` に段階と途中の値 |
| | `GET /api/estimates`、`GET /api/estimates/{id}`、`PUT /api/estimates/{id}`（`version` 必須）、`POST /api/estimates/{id}/chat` | 詳細は条件・解析（確定／概算／解析不可）・`result`（明細・費目ごとの小計・price・missing・hints・items・shape）・`blockers`（帳票の種類ごとの発行できない理由）・類似実績・修正履歴・発行した帳票 |
| 帳票 | `GET /api/estimates/{id}/documents/preview.png?kind=&template_id=`、`POST /api/estimates/{id}/documents`（201、未入力・未受注は 409 `NOT_ISSUABLE` と `details`）、`GET /api/issued`、`GET /api/issued/{id}/file.pdf` | |
| 案件 | `GET /api/cases`（一覧・ステータス・警告の数）、`PUT /api/cases/{id}/status`（`version` 必須、失注は理由）、`GET /api/cases/{id}/history`、`GET/PUT /api/statuses`、`GET /api/review?months=` | |
| 検索・書類・取引先 | `GET /api/search?q=&kind=`、`GET/POST /api/library`、`GET /api/library/{id}/file`、`DELETE /api/library/{id}`、`GET/POST/PUT /api/partners` | |
| 設定 | `GET/PUT /api/staff`、`GET/POST/PUT/DELETE /api/categories`、`GET/PUT /api/attributes`、`GET/POST/PUT/DELETE /api/templates`、`GET /api/templates/{id}/preview.png`、`GET/POST/PUT /api/master-tables/{table}`、`GET /api/logic`、`POST /api/history/columns` | |

**同時利用**：更新は開いたときの `version` を送る。違えば 409 `CONFLICT`（上書きしない）。データベース側でも版を照合して更新する。**操作者**：`X-Actor`（画面で選んだ担当者、URL エンコード）を `*_by`・修正履歴・ステータスの履歴に残す。ログインを足すときは `api/security.py` の `current_actor` と `token_guard` を差し替える。

## 今までのエンドポイント（画面を使わない呼び出し）

| 機能 | メソッドとパス | 説明 |
|---|---|---|
| 状態 | `GET /api/health` | 起動確認と、図面PDFの読み取りが使えるか（トークン不要） |
| マスター | `GET /api/masters` | 材料・工程・表面処理・価格方針・自社情報 |
| アップロード | `POST /api/files`（multipart） | STEP・DXF・図面PDF。返り値の `file_id` を以降で使う |
| 形状の解析 | `POST /api/analyses` → `GET /api/jobs/{job_id}` | 受付番号をすぐ返す（202）。状態は queued / running / done / failed、結果は `SheetMetalAnalysis` |
| 図面PDFの読み取り | `POST /api/drawings/readings` → `GET /api/jobs/{job_id}` | 同じく受付番号方式。結果の `drawing`（項目ごとの状態つき）を見積の入力に戻す。APIキーがなければ 503 `DRAWING_READER_UNAVAILABLE` |
| 見積 | `POST /api/quotes` | 解析（受付番号か結果そのもの）と条件から、原価の内訳・単価・小計・税・合計・確認が必要な点 |
| 見積書 | `POST /api/documents` → `GET /api/documents/{quote_no}/{quote,internal}.pdf` | 見積番号の採番、PDFの作成と保存、履歴への記録 |
| 類似見積 | `POST /api/similar-quotes` | 最大5件（理由・違い・価格の比較）と参考単価 |
| 過去見積の履歴 | `POST /api/history/import`（multipart、`mapping` はJSON）、`GET /api/history`、`GET /api/history/{quote_no}`、`PUT /api/history/{quote_no}/outcome` | 取り込み・一覧（顧客・図番・取り込み元で絞り込み、ページ送り）・詳細（元の行の全項目）・受注／失注 |
| 3D表示 | `GET /api/files/{file_id}/model.glb` | STEP を glTF（GLB、単位 mm）にして返す。一度作ったものは保存して再利用 |

エラーは `{"error": {"code": "...", "message": "..."}}`。入力の誤りは 400/404/409/413/415/422、未知の例外は 500 で、内部のパスや秘密情報は返さない（詳細はサーバーのログにだけ出す）。

## 呼び出しの流れ

### STEP から見積書まで

```
POST /api/files                    {file}                         → {file_id}
POST /api/analyses                 {file_id, k_factor, k_factor_confirmed}   → 202 {job_id, status: queued}
GET  /api/jobs/{job_id}            （done になるまで数秒おきに）   → {status: done, result: SheetMetalAnalysis}
GET  /api/files/{file_id}/model.glb                               → 3D表示
POST /api/quotes                   {analysis_job_id, condition}   → {price: {unit_price, subtotal, tax, total}, is_estimate, estimate_reasons, quote}
POST /api/similar-quotes           {analysis_job_id, condition, customer, drawing_no}  → {reference, matches[]}
POST /api/documents                {analysis_job_id, condition, recipient, part, include_internal}
                                                                   → {quote_no, files[{url}]}
GET  /api/documents/{quote_no}/quote.pdf                          → 見積書PDF
PUT  /api/history/{quote_no}/outcome  {outcome: 受注}              → 受注の記録
```

DXF は `POST /api/analyses` に `thickness_mm` が必要（DXFには板厚がない）。曲げ線のない展開図は、平板と曲げ線の描き漏れを区別できないため概算になる。平板と確認したときは `flat_confirmed: true` を付ける。

### 図面PDFがあるとき

```
POST /api/files {図面PDF}                → {file_id}
POST /api/drawings/readings {file_id}   → 202 {job_id}
GET  /api/jobs/{job_id}                 → {result: {reading, drawing: {items[{field, value, status, reasons}], flags, unregistered_texts, ...}}}
   画面で items を確認・修正し、確定にした項目は status を「確定」にする
POST /api/quotes {analysis_job_id, condition: {material（仮の材質）, quantity・surface_treatment・rush（指定したものだけ図面の値に代わり確定）,
                  additional_processes（手で足す加工。図面と同じ加工は手入力の個数で置き換え、二重に数えない）}, drawing}
   → 「確定」の項目だけが金額に入り、確認が必要な点は estimate_reasons に並ぶ
   マスターにない加工は金額に入れず、estimate_reasons と見積書の備考に「別途見積」
POST /api/documents {..., drawing}      → 作成する時点で drawing の項目をすべて確定として扱い、つねに「御見積書」
   （数量が決まっていなければ 400 QUANTITY_REQUIRED。マスター未登録の加工は備考に「別途見積」）
```

### 過去見積の取り込み

```
POST /api/history/import  file=旧見積.csv  mapping={"customer": "得意先コード名"}  → {read, added, skipped, problems}
GET  /api/history?customer=...&limit=50&offset=0
```

## 受付（重い処理）の仕組み

- `JobStore` が受付ごとに `<ESTIMATE_STORAGE_DIR>/jobs/<job_id>.json` を持つ（種類、入力、状態、時刻、結果か失敗の理由）。受け付けたら書いてすぐ返す（テストでは処理に3秒かかっても受付は0.5秒以内）
- `JobQueue` が同じプロセスのスレッドで実行する（`JOB_WORKERS` 本）
- **再起動**：起動時に queued / running のままの受付を並べ直す（アップロードしたファイルは残っている）。完了した受付の結果はそのまま読める（`tests/test_api.py::test_jobs_survive_a_restart`）

## 設定（環境変数）

| 変数 | 既定 | 内容 |
|---|---|---|
| `DATABASE_URL` | なし（SQLite `<ESTIMATE_STORAGE_DIR>/app.db`） | データベース |
| `ESTIMATE_DATA_DIR` | `data` | 初期データのマスター（材料・工程・表面処理・価格方針・自社情報） |
| `ESTIMATE_STORAGE_DIR` | `output` | アップロード（uploads/）、受付（jobs/）、見積書（documents/）、3D表示（viewer/） |
| `PAST_QUOTES_PATH` | `data/past_quotes/history.csv` | 初期データの見積履歴（コミットする。データベースに取り込んだ後は書き換えない） |
| `QUOTE_LOG_PATH` | `<ESTIMATE_STORAGE_DIR>/quote_log.csv` | 見積書の出力記録 |
| `API_TOKEN` | なし | 設定すると API の利用に `Authorization: Bearer <token>` か `X-API-Token` が必要（`/api/health` を除く） |
| `MAX_UPLOAD_MB` | 50 | アップロードの上限 |
| `JOB_WORKERS` | 2 | 受付を処理するスレッド数 |
| `ANALYSIS_ANTHROPIC_API_KEY` | なし | 図面PDFの読み取りにだけ使う（LLM を使う機能は増やしていない） |
| `DRAWING_READER` | なし | `recorded:<file.json>` で記録済みの読み取り結果を返す（お試し・撮影・通しのテスト用） |
| `WEB_DIST` | `web/dist` | API が配信する画面 |

アップロードは拡張子（.step .stp .dxf .pdf、履歴の取り込みは .csv）と中身の先頭（STEP は `ISO-10303-21`、PDF は `%PDF-`、DXF は `SECTION`）の両方を確かめる。ファイル名は正規化し、保存場所は受付側で決める（利用者の入力からパスを作らない）。

## 差し替えられる部分

| 部分 | 今 | 差し替え先 | 差し替え方 |
|---|---|---|---|
| 受付の実行 | `JobQueue`（同じプロセスのスレッド） | RQ・Celery などの処理待ちの列と別プロセスの worker | `JobQueue.submit` を「列に入れる」に替え、worker が `JobQueue.execute(job_id)` を呼ぶ |
| 受付の状態 | `JobStore`（JSONファイル） | データベースの表 | `create / read / update / unfinished` を実装し直す |
| ファイル | `LocalFileStore`（`ESTIMATE_STORAGE_DIR`） | オブジェクトストレージ（S3互換、社内なら MinIO） | `FileStore`（`save / get / path / meta`）を実装し直す |
| 認証 | 共通の合言葉（`API_TOKEN`）と、画面で選んだ担当者 | ログイン・権限・利用者の招待 | `api/security.py` の2つの関数を差し替える。`*_by` の列はそのまま使う |
| データベース | SQLite（既定）／PostgreSQL（`DATABASE_URL`） | 管理サービスの PostgreSQL | `DATABASE_URL` を替えるだけ |
| 置き方 | `docker compose`（db・api・web） | 工場ごとの社内サーバー、または複数工場のクラウド | どちらもこの構成で動く。クラウド固有のサービスには依存していない |

## 今の制限

- `JobQueue` は1つのプロセスの中で動くため、API を複数台に増やすときは処理待ちの列が要る（受付の状態もファイルなので、複数台では共有のディスクかデータベースに移す）
- 図面PDFの読み取りは今の読み取り器を呼ぶだけ（LLM の呼び出しの中身は変えていない）。pdfium は同時に使えないため、ページの読み込みとプレビュー・文字の取り出しは1つずつ行う
- 図面の検索・類似の候補は、図面をすべて読んでから絞り込む（数千件までを想定）。それ以上はデータベースの索引と検索に移す
