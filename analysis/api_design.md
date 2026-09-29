# API の設計（段階1：サーバー部分の切り出し）

2026-09-28。受入条件は [handoff_api.md](handoff_api.md)。仕様書は API を起動して `http://localhost:8000/docs`（OpenAPI は `/openapi.json`）で見られる。

## 構成

```
React の画面（段階2）       Streamlit のデモ（app.py）
        │ HTTP（JSON）                │ 関数呼び出し（同じプロセス）
        ▼                             │
  api/main.py（FastAPI）              │
   入出力の型・認証・制限・エラー     │
        │                             │
        └──────────┬──────────────────┘
                   ▼
  src/services/（処理の層。Web の枠組みに依存しない）
   estimate.py  EstimateService：解析・図面の読み取り・条件の組み立て・見積・見積書・類似見積・履歴・3D表示
   schemas.py   入出力の型（pydantic）。src/models.py の既存モデルはそのまま使う
   jobs.py      受付（JobStore：状態をファイルに保存）と実行（JobQueue：プロセス内のスレッド）
   storage.py   アップロードしたファイルの置き場（LocalFileStore）
   settings.py  置き場所と制限（環境変数）
   gltf.py      STEP → GLB（3D表示用）
                   ▼
  既存の処理：sheetmetal_analyzer・dxf_analyzer・pdf_reader・pdf_quote・quote_engine・quote_document・
            quote_pdf・quote_log・past_quotes・similar_quotes（中身は変えていない）
```

- **処理を2か所に書かない**：`app.py` にあった処理の組み立て（解析器の選択、図面の条件から見積条件を作る、見積と金額、見積書の採番・描画・保存・履歴への記録、類似見積、受注・失注）は `EstimateService` に移した。画面と API は同じメソッドを呼ぶ
- **金額**：`EstimateService.price` だけが計算する（`QuoteEngine` の最終価格 → 単価は1円未満切り上げ、消費税は切り捨て）。画面の表示、`POST /api/quotes`、見積書PDFが同じ値になることをテストで確かめている（`tests/test_api.py::test_api_screen_and_document_show_the_same_amounts`）
- 解析・見積の結果は変えていない（処理の場所を移しただけ）。STEP・DXFの評価は合格のまま

## エンドポイント

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
| `ESTIMATE_DATA_DIR` | `data` | マスター（材料・工程・表面処理・価格方針・自社情報） |
| `ESTIMATE_STORAGE_DIR` | `output` | アップロード（uploads/）、受付（jobs/）、見積書（documents/）、3D表示（viewer/） |
| `PAST_QUOTES_PATH` | `data/past_quotes/history.csv` | 見積履歴（コミットする） |
| `QUOTE_LOG_PATH` | `<ESTIMATE_STORAGE_DIR>/quote_log.csv` | 見積書の出力記録 |
| `API_TOKEN` | なし | 設定すると API の利用に `Authorization: Bearer <token>` か `X-API-Token` が必要（`/api/health` を除く） |
| `MAX_UPLOAD_MB` | 50 | アップロードの上限 |
| `JOB_WORKERS` | 2 | 受付を処理するスレッド数 |
| `ANALYSIS_ANTHROPIC_API_KEY` | なし | 図面PDFの読み取りにだけ使う（このPhaseで LLM を使う機能は増やしていない） |

アップロードは拡張子（.step .stp .dxf .pdf、履歴の取り込みは .csv）と中身の先頭（STEP は `ISO-10303-21`、PDF は `%PDF-`、DXF は `SECTION`）の両方を確かめる。ファイル名は正規化し、保存場所は受付側で決める（利用者の入力からパスを作らない）。

## 段階2・3で差し替える部分

| 部分 | 今（段階1） | 差し替え先 | 差し替え方 |
|---|---|---|---|
| 画面 | Streamlit（`app.py`、処理の層を直接呼ぶ） | React（Next.js、TypeScript）が API を呼ぶ（段階2） | OpenAPI から型を生成できる（`/openapi.json`）。Streamlit は段階3で廃止 |
| 受付の実行 | `JobQueue`（同じプロセスのスレッド） | RQ・Celery などの処理待ちの列と別プロセスの worker | `JobQueue.submit` を「列に入れる」に替え、worker が `JobQueue.execute(job_id)` を呼ぶ。ハンドラ（`EstimateService.run_analysis_job` など）はそのまま |
| 受付の状態 | `JobStore`（JSONファイル） | PostgreSQL の表（段階3） | `create / read / update / unfinished` の4つを実装し直す |
| アップロード・見積書・3D表示のファイル | `LocalFileStore`、`output/documents/` | オブジェクトストレージ（S3互換など。社内なら MinIO） | `FileStore`（`save / get / path`）を実装し直す。見積書の保存も同じ形に寄せる |
| 見積履歴 | `data/past_quotes/history.csv`（`HistoryStore`） | PostgreSQL（段階3） | `HistoryStore`（`load / append / set_outcome`）を実装し直す。検索の索引（`SimilarQuoteSearch`）はそのまま使える |
| 見積番号の採番 | `output/quote_log.csv` ＋ ロックファイル | データベースの連番（段階3） | `QuoteLog.issue` を置き換える |
| 認証 | 共通の合言葉（`API_TOKEN`） | ログイン（利用者ごとの権限、段階3） | `require_token` を置き換える。利用者ごとのデータの分離は、各ストアに利用者（工場）を持たせる |
| マスター・自社情報 | `data/*.csv`（`ESTIMATE_DATA_DIR`） | 工場ごとの設定（段階3） | `MasterLoader` と `load_company` の読み込み元を替える |
| 置き方 | Docker（API とデモ） | 工場ごとの社内サーバー、または複数工場のクラウド | どちらもこの Dockerfile で動く。クラウド固有のサービスには依存していない |

## 今の制限

- `JobQueue` は1つのプロセスの中で動くため、API を複数台に増やすときは処理待ちの列（段階3）が要る
- 履歴の CSV は、API とデモがまったく同時に書くと、先の書き込みが失われることがある（ファイル自体は一時ファイルからの置き換えなので壊れない）。複数人での利用はデータベース（段階3）で扱う
- 図面PDFの読み取りは今の読み取り器を呼ぶだけ（LLM の呼び出しの中身は変えていない）
