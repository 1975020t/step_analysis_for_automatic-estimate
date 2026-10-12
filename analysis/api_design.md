# API の設計

2026-09-28（段階1：サーバー部分の切り出し、[handoff_api.md](handoff_api.md)）、2026-10-05（画面とデータベース、[handoff_ui.md](handoff_ui.md)）、2026-10-07（画面の改修：ホーム・新規見積の4ステップ・次のアクション・フェーズ）。仕様書は API を起動して `http://localhost:8000/docs`（OpenAPI は `/openapi.json`）で見られる。

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
   cases.py     案件・進捗・警告・フェーズ・次のアクション・To Doリスト（ホーム）・ステータス設定・実績分析
   similar.py   類似の図面と実績（src/similar_quotes.is_similar）
   library.py   書類と検索、admin.py 設定とマスター編集と見積ロジック、masters.py DB のマスター
   core.py      Platform（DB・マスターと履歴の読み込み直し・受付）
        ▼
  src/services/（処理の層。Web の枠組みに依存しない）
   estimate.py  EstimateService：解析・図面の読み取り・見積・見積書・類似見積・履歴・3D表示（今までの API もこれを呼ぶ）
   jobs.py      受付（状態をファイルに保存、スレッドで実行、途中の進み具合）
   storage.py   ファイルの置き場（FileStore。中身はフォルダか S3 の BlobStore）、settings.py 置き場所と制限、pdfium_lock.py
        ▼
  src/db/（SQLAlchemy のモデル・接続・初期データ）、migrations/（Alembic）
  既存の処理：sheetmetal_analyzer・dxf_analyzer・pdf_reader・pdf_quote・quote_engine・quote_document・
            quote_pdf・quote_log・past_quotes・similar_quotes（解析と金額の計算は変えていない）
```

- **処理を2か所に書かない**：画面は API だけを呼ぶ。金額（費目ごとの小計を含む）、未入力の項目、発行できるか（帳票の種類ごとの理由）、類似かどうか（今回との単価の差も）、フェーズ、To Doリストに載せるか（理由と急ぐ順）、次のアクション、見積の手順の現在地は API が返し、画面は表示するだけ。画面・API・帳票PDFで金額が一致することをテストで確かめている（`tests/test_platform.py`、`tests/test_e2e_flow.py`）
- **解析と金額の計算は変えていない**：変えたのは見積の条件の扱い（未登録の単価・解析不可のときの形状の値の入力、発行できる条件、希望納期）と類似の条件だけ。入力した単価・形状の値でも `QuoteEngine` がルールで計算する
- **今までの API**（`/api/quotes`・`/api/documents`・`/api/similar-quotes`・`/api/history`）も動く。マスターと履歴はデータベースのものを使う

## 画面用のエンドポイント

| 機能 | メソッドとパス | 説明 |
|---|---|---|
| 選択肢 | `GET /api/meta`、`GET /api/recent` | 材質・表面処理・追加加工・担当者・ステータス（フェーズつき）・フェーズの一覧・分類・属性・テンプレート・読み取りの可否／最近の見積（今の画面は使っていない） |
| ホーム | `GET /api/home` | To Doリスト（`todo`：理由 `todo`・一言 `todo_note`・次のアクション `next_action`、急ぐ順）、フェーズごとの件数と注意（`phases`）、メニューの件数（`counts`） |
| 図面 | `POST /api/drawings/register`（202） | 1件＝図面PDF・形状ファイル（片方でもよい）と図番・品名・顧客・改訂・分類・版の指定。形状解析・PDFの文字・読み取り結果の保存は受付番号（register）で |
| | `GET /api/drawings`（条件はクエリ。自社の属性は `attrs` に JSON）、`GET /api/drawings/same-number`、`GET/PUT /api/drawings/{id}`、`PUT /api/drawings/{id}/current-revision`、`GET /api/drawings/{id}/similar`、`POST /api/drawings/{id}/memos`、`POST /api/revisions/{id}/notes`、`DELETE /api/notes/{id}`、`GET /api/revisions/{id}/preview.png`、`GET /api/files/{id}/raw` | |
| 見積 | `GET /api/estimate-draft?drawing_id=` または `?reading_job_id=&file_name=` | 新規見積の条件入力の下書き。項目ごとに値と印 `mark`（read 読取・review 要確認・unregistered マスタ未登録・none 記載なし・drawing 図面情報）と `note`。値は見積の受付が図面の読み取りから入れる値と同じ（`inputs_from_reading`） |
| | `POST /api/estimates`（202。`rush`・`processes`・`custom_processes` も受ける） | 案件と見積を作り、受付番号（estimate）で 形状解析 → 図面の読み取り → マスタ照合 → 見積計算 → 類似実績の検索。`GET /api/jobs/{id}` の `progress` に段階と途中の値 |
| | `GET /api/estimates`、`GET /api/estimates/{id}`、`PUT /api/estimates/{id}`（`version` 必須）、`POST /api/estimates/{id}/chat` | 詳細は条件・解析（確定／概算／解析不可）・`result`（明細・費目ごとの小計・price・missing・hints・items・shape）・`hint_items`（確認事項と直す場所 `field`）・`blockers`（帳票の種類ごとの発行できない理由）・`notices`（発行は止めない注意。見積書の発行後に条件が変わったとき、納品書・請求書は発行済みの見積書の金額で作る旨）・類似実績と `similar_compare`（今回との単価の差）・`next_action`・`flow_step`（6つの手順の現在地）・`outcome_statuses`（受注・失注のステータス）・`phase`・修正履歴・発行した帳票 |
| 帳票 | `GET /api/estimates/{id}/documents/preview.png?kind=&template_id=`、`POST /api/estimates/{id}/documents`（201、未入力・未受注は 409 `NOT_ISSUABLE` と `details`）、`GET /api/issued`、`GET /api/issued/{id}/file.pdf` | |
| 案件 | `GET /api/cases?q=&staff=&phase=`（一覧＝急ぐ順・フェーズ・警告・To Doの理由・次のアクション、フェーズごとの件数、警告の数。`phase` はフェーズのキーか `active`）、`PUT /api/cases/{id}/status`（`version` 必須、失注は理由）、`GET /api/cases/{id}/history`、`GET/PUT /api/statuses`、`GET /api/review?months=` | |
| 検索・書類・取引先 | `GET /api/search?q=&kind=`、`GET/POST /api/library`、`GET /api/library/{id}/file`、`DELETE /api/library/{id}`、`GET/POST/PUT /api/partners` | |
| 設定 | `GET/PUT /api/staff`、`GET/POST/PUT/DELETE /api/categories`、`GET/PUT /api/attributes`、`GET/POST/PUT/DELETE /api/templates`、`GET /api/templates/{id}/preview.png`、`GET/POST/PUT /api/master-tables/{table}`、`GET /api/logic`、`POST /api/history/columns` | |

**フェーズ**：ステータスの属性 `phase`（`drafting` 見積作成中・`checking` 見積確認中・`waiting` 回答待ち・`production` 製造・出荷・`closed` 完了分）。名前を変えても追加しても、属するフェーズはステータスが持つ（マイグレーション `0002` が、改修前の表示グループ・役割・名前から決めて移す）。受注かどうか（`outcome`）は、役割が受注・出荷済・完了か、フェーズが製造・出荷のステータスで決まる。完了分の役割のないステータス（利用者が追加した「納品済」など）は、それまでの結果を引き継ぐ。納品書・請求書は結果が受注で見積書を発行済みのときだけ発行できる。**To Do・注意の判定** は案件の警告（`cases.warnings_of`）と同じ基準で、二重に作らない。

**入力の誤り**：422（`INVALID_INPUT`）と見積の条件の誤り（400）は、欄の名前と直し方を日本語で返す（`wording.input_errors`）。マスタの計算のしかた・単位は決まった値だけ、価格方針の率は 0〜1 だけを受け付ける。展開図DXFに板厚がないときの未入力の項目は、寸法5つではなく `dxf_thickness`（板厚）1つにする。

**連番**：案件番号・納品書・請求書の番号と、見積書の再発行の枝番は、データベースが `value = value + 1` で数える（`core.bump`）。以前は値を読んで足した値を書いていたため、同時に保存すると同じ番号になり、後の保存がサーバーエラーで失敗していた（同じデータでも失敗したりしなかったりした原因）。

**受付の開始**：見積の受付は、見積の行を保存してから始める（`JobQueue.submit(..., start=False)` → コミット → `start`）。保存前に受付が見積を読みに行って「見積が見つかりません」となることを防ぐ。

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

- 受付はデータベースの `jobs` 表に1行ずつ持つ（`DbJobStore`。種類、入力、状態、時刻、進み具合、結果か失敗の理由）。以前の版の `<ESTIMATE_STORAGE_DIR>/jobs/*.json` は起動時に1回だけ取り込む。SQLite のときは受付だけ別のファイル（`jobs.db`）に置く（処理中の見積が `app.db` の書き込みの鍵を持ったままでも、進み具合を書けるように）。受け付けたら書いてすぐ返す（テストでは処理に3秒かかっても受付は0.5秒以内）
- `JobQueue` が同じプロセスのスレッドで実行する（`JOB_WORKERS` 本）
- 画面の受付（登録 `register`・見積 `estimate`）は3つの段に分かれている：**準備**（`prepare_register`・`prepare_estimate`。データベースを読み、どのファイルをどの条件で解析・読み取りするかを JSON の「計画」にする）→ **抽出**（`src/services/extraction.py` の `extract`。ファイルと読み取り器だけを使い、データベースは使わない。結果も JSON）→ **保存**（`finish_register`・`finish_estimate`。マスター照合・金額・類似・保存）。今は1つの受付の中で順に呼ぶ。AWS ではそれぞれを Step Functions の段にでき、抽出の段にはデータベースの権限が要らない（`tests/test_extraction.py` が、抽出がデータベースを読み込まないことと、各段を JSON で受け渡しても同じ結果になることを確かめる）
- **再起動**：起動時に queued / running のままの受付を並べ直す（アップロードしたファイルは残っている）。完了した受付の結果はそのまま読める（`tests/test_api.py::test_jobs_survive_a_restart`）

## 設定（環境変数）

| 変数 | 既定 | 内容 |
|---|---|---|
| `DATABASE_URL` | なし（SQLite `<ESTIMATE_STORAGE_DIR>/app.db`） | データベース（PostgreSQL、Aurora DSQL は `auroradsql+psycopg://admin@<エンドポイント>/postgres?region=...`） |
| `ESTIMATE_DATA_DIR` | `data` | 初期データのマスター（材料・工程・表面処理・価格方針・自社情報） |
| `ESTIMATE_STORAGE_DIR` | `output` | アップロード（uploads/）、見積書（documents/）、3D表示（viewer/）、SQLite の `app.db` |
| `ESTIMATE_STORAGE_URL` | なし | `s3://<バケット>/<接頭辞>` で uploads/・documents/・viewer/ を S3 に置く（SQLite の `app.db` はフォルダのまま） |
| `PAST_QUOTES_PATH` | `data/past_quotes/history.csv` | 初期データの見積履歴（コミットする。データベースに取り込んだ後は書き換えない） |
| `QUOTE_LOG_PATH` | `<ESTIMATE_STORAGE_DIR>/quote_log.csv` | 以前の版の見積書の出力記録（起動時にデータベースの `quote_log` 表へ取り込む） |
| `API_TOKEN` | なし | 設定すると API の利用に `Authorization: Bearer <token>` か `X-API-Token` が必要（`/api/health` を除く） |
| `TENANT_TOKENS` | なし | 会社ID と合言葉（`alpha=合言葉1,beta=合言葉2`）。下の「複数の会社」 |
| `COGNITO_USER_POOL_ID`・`COGNITO_CLIENT_ID` | なし | 両方あるとログイン（Cognito）を使う。下の「ログイン」 |
| `COGNITO_DOMAIN` | なし | ログインの画面のドメイン（`https://<接頭辞>.auth.<リージョン>.amazoncognito.com`） |
| `COGNITO_TENANT_CLAIM` | `custom:tenant_id` | 会社IDを持つ属性 |
| `COGNITO_ISSUER` | ユーザープールから決まる | テストで偽のユーザープールを使うときだけ |
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
| 受付の状態 | `DbJobStore`（データベースの `jobs` 表） | 済み | `src/services/platform/records.py`。見積書の出力記録も `DbQuoteLog`（`quote_log` 表、番号の重なりは主キーで防ぐ） |
| ファイル | `FileStore` ＋ `LocalBlobStore`（`ESTIMATE_STORAGE_DIR`）か `S3BlobStore`（`ESTIMATE_STORAGE_URL`） | 済み（S3。S3互換の MinIO も同じ） | `BlobStore`（`put / get / exists / local_file`）。CadQuery・pdfium のようにファイルを自分で開く処理には `FileStore.local_path` が手元のパスを渡す（S3 では一時的な写し） |
| 認証 | ログイン（Cognito、`COGNITO_*`）。ほかのシステムは会社ごとの合言葉（`API_TOKEN`・`TENANT_TOKENS`） | 済み（ログイン）。権限（役割）・利用者の招待は未 | `api/login.py` が ID トークンを確かめ、`api/security.py` が会社と操作者を決める。`*_by` の列はそのまま使う |
| データベース | SQLite（既定）／PostgreSQL／Aurora DSQL（`DATABASE_URL`） | Aurora DSQL | `DATABASE_URL` を替えるだけ。DSQL への対応は下の「Aurora DSQL で動かす」 |
| 置き方 | `docker compose`（db・api・web） | 工場ごとの社内サーバー、または複数工場のクラウド | どちらもこの構成で動く。クラウド固有のサービスには依存していない |

## 複数の会社（マルチテナント）

1つのデータベースとファイル置き場を、複数の会社で分けて使う（プール型）。

- 会社は合言葉で決まる（`TENANT_TOKENS`、`API_TOKEN` は会社ID `default`）。要求ごとに `TenantMiddleware`（`api/security.py`）が会社を決め、処理の間ずっと `current_tenant()`（`src/tenancy.py`）で参照できる。画面用の API と業務の処理は会社を意識しない
- 会社のデータの表にはすべて `tenant_id` がある（`TenantScoped`、マイグレーション 0005。今までのデータは `default`）。セッションの読み書きには、今の会社の行だけを対象にする条件が自動で付き（`src/db/tenant_filter.py`）、新しい行には今の会社が入る。ほかの会社の番号を指定しても 404
- 図番・案件番号・見積番号・帳票番号・担当者名は会社の中で重ならなければよい。番号の数え（`counters`）・マスター・自社情報・見積書の出力記録は、主キーに会社を含む。番号は会社ごとに1から振る
- ファイルは `tenants/<会社ID>/` の下（S3 でも同じ。`default` は今までの場所のまま）
- 受付（バックグラウンドの処理）は、受け付けた会社を受付の中に持ち、その会社として動く。受付番号の照会もほかの会社のものは 404
- 新しい会社は最初に呼ばれたときに、マスター（`data/*.csv`）・ステータス・属性・テンプレートが入る。自社情報は空（画面で入れる）。過去見積・協力会社のお試しデータは入れない
- マスターと類似見積の索引の作り置きは会社ごと

ログインを使うときは、ログインした利用者の会社（ID トークンの `custom:tenant_id`）で決まる（下の「ログイン」）。

## ログイン（Amazon Cognito）

`COGNITO_USER_POOL_ID` と `COGNITO_CLIENT_ID` を設定すると使う。

- 画面（`web/src/auth.ts`）は最初に `GET /api/auth/config`（守りなし）で方式を知る。`{"mode": "login", "client_id", "scopes", "authorize_url", "token_url", "logout_url"}` なら、ログインの画面を出し、Cognito のログインの画面（Hosted UI / Managed Login）へ移る。認可コードと PKCE（S256）で、画面にシークレットを置かない。パスワードは本システムのサーバーを通らない
- 戻り先は `/auth/callback`（`state` を照合し、コードを ID トークン・リフレッシュトークンに替える）。ID トークンは `localStorage` に置き、期限の1分前にリフレッシュトークンで取り直す。API が 401 を返したらログインし直す
- API の呼び出しは `Authorization: Bearer <ID トークン>`。`api/login.py`（`CognitoVerifier`）がユーザープールの鍵（`<issuer>/.well-known/jwks.json`、知らない鍵 ID のときだけ取り直す）で署名を確かめ、発行元・アプリクライアント（`aud`）・ID トークンであること（`token_use`）・期限（30秒の幅）を確かめる
- 会社は `custom:tenant_id`（`COGNITO_TENANT_CLAIM`）。ない・形が正しくない利用者は 403 `NO_TENANT`。初めての会社には初期データが入る（合言葉の会社と同じ）
- 操作者（`*_by` の列と履歴）はログインした人の `name`（なければメールアドレス）。`X-Actor` は使わない。`GET /api/me` が名前・メール・会社を返す
- 合言葉（`API_TOKEN`・`TENANT_TOKENS`）も同時に使える（ほかのシステム・スクリプト用。先に合言葉として照合する）
- ログアウトは画面のトークンを消し、Cognito の `/logout`（`logout_uri` は画面の `/`）へ移る
- ユーザープール側の設定：アプリクライアントはシークレットなし、許可するフローは認可コード、スコープ `openid email profile`、コールバック `https://<画面>/auth/callback`、サインアウト `https://<画面>/`、カスタム属性 `tenant_id`（利用者が書き換えられないようにする）
- 試験は AWS を呼ばない：`tests/test_login.py` は自前の鍵で ID トークンを作り、`tests/test_e2e_login.py` は偽の鍵の配布元（`COGNITO_ISSUER`）とブラウザの通信の差し替えでログインからログアウトまでを通す

## Aurora DSQL で動かす

`DATABASE_URL=auroradsql+psycopg://admin@<エンドポイント>/postgres?region=ap-northeast-1` で Aurora DSQL につなぐ（方言は公式の `aurora-dsql-sqlalchemy`）。DSQL は PostgreSQL と同じ書き方で動くが、次の点が違うので合わせてある。

| DSQL の違い | 対応 |
|---|---|
| パスワードの代わりに IAM の一時トークン | 接続のたびにコネクタが作る（Lambda のロールの権限）。1時間で切れる接続は 50分で作り直す（`src/db/session.py`） |
| 構造の変更（DDL）は1文ずつ、データの変更と同じトランザクションにできない | 空のデータベースには、モデルから表を一度に作り（`create_all`、方言が1文ずつ確定）、最新のマイグレーションとして記録する。モデルとマイグレーションが同じ形になることはテストで確かめる。以後のマイグレーションは DSQL でできる変更だけにする |
| 番号（ID）は 64ビットで、接続ごとに 65,536 個ずつ先取りされる（番号が飛ぶ） | 番号と、番号を指す列をすべて `BIGINT` にした（マイグレーション 0004。SQLite は INTEGER のまま）。画面は番号の連続を前提にしていない |
| 鍵で待たない。同じ行を同時に変えると、後から確定した方が失敗する（SQLSTATE 40001） | 画面の操作は、まだ何も確定していなければ最初からやり直す（最大5回、`api/conflict.py`）。何か確定した後なら 409 で「もう一度」と返す。受付の DB の段（準備・仕上げ）、受付の状態の書き込み、見積番号もやり直す（`src/db/retry.py`） |
| 1つのトランザクションで変えられるのは 3,000行まで | 過去見積の初期データ（約2,100行）と取り込みは 1,000行ずつ確定する |

DSQL の実機では未確認（手元で動く DSQL はない）。AWS に置くときに、お試しデータの投入と画面の巡回を実機で通す。

## 今の制限

- `JobQueue` は1つのプロセスの中で動くため、API を複数台に増やすときは処理待ちの列が要る（受付の状態と出力記録はデータベースにあるので、複数台で共有できる）
- 図面PDFの読み取りは今の読み取り器を呼ぶだけ（LLM の呼び出しの中身は変えていない）。pdfium は同時に使えないため、ページの読み込みとプレビュー・文字の取り出しは1つずつ行う
- 図面の検索・類似の候補は、図面をすべて読んでから絞り込む（数千件までを想定）。それ以上はデータベースの索引と検索に移す
