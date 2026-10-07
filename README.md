# 図面管理・板金見積システム

STEP/STP形式の一定板厚板金（と展開図DXF・図面PDF）を解析し、ルールベースで見積を出すシステムです。図面を登録し、登録した図面から見積を作り、案件として進捗を追い、見積書・納品書・請求書を発行し、受注・失注を振り返るまでを、ブラウザの画面（React）でつなげて使えます。データはデータベース（PostgreSQL。テストと簡易起動は SQLite）に保管します。対象外形状に推定値を強制せず、項目別の方式・信頼度・警告・理由コードを返します。

要件定義書・設計書・画面設計書・操作マニュアル・評価報告書・解説資料は [docs/](docs/README.md)（PDFは `docs/pdf/`）にあります。

## 起動

### Docker で起動する（データベース・API・画面）

Docker（Windows なら Docker Desktop）があれば、社内サーバーでもクラウドの仮想マシンでも同じ手順で起動できます。PowerShell で：

```powershell
cd step_analysis_for_automatic-estimate
docker compose up --build -d      # 初回はイメージの作成に10分ほど
start http://localhost:8080       # 画面。API の仕様書は http://localhost:8000/docs
docker compose logs -f api        # 動作の記録を見る
docker compose down               # 停止（データは残る）
```

- 起動するのは3つです：`db`（PostgreSQL 16、データはボリューム `dbdata`）、`api`（FastAPI、`http://localhost:8000`）、`web`（画面。nginx、`http://localhost:8080`。`/api` は API に中継）
- API は起動時にデータベースの構造を最新にし（マイグレーション、`alembic upgrade head`）、空の表にだけ初期データ（`data/*.csv` のマスターと自社情報、`data/past_quotes/history.csv` の過去見積、案件ステータス14個など）を入れます。2回目以降の起動では、画面で変えた内容を上書きしません
- 図面PDF・形状ファイル・帳票PDF・書類は `output/`（ホストのフォルダ）に保管します。置き場所は `ESTIMATE_STORAGE_DIR` で変えられます
- データベースのユーザー・パスワードは `POSTGRES_USER`・`POSTGRES_PASSWORD`（既定はどちらも `estimate`。本番では変えてください）。例：`$env:POSTGRES_PASSWORD="..."` のあと `docker compose up -d`
- API に合言葉をかけるときは `$env:API_TOKEN="..."`。画面では「設定 ＞ 担当者」で同じ合言葉を入れます。図面PDFの読み取りを使うときは `$env:ANALYSIS_ANTHROPIC_API_KEY="..."`
- 画面のポートは `WEB_PORT`（既定 8080）。Docker Hub に届かない社内ネットワークでは、`BASE_IMAGE`（Python）・`NODE_IMAGE`・`NGINX_IMAGE`・`POSTGRES_IMAGE` に社内レジストリのイメージを指定します（例 `$env:BASE_IMAGE="registry.example.local/python:3.11-slim"`）

### Docker なしで起動する（開発・お試し）

Python 3.11 と Node.js 20 以上が必要です。データベースは指定しなければ SQLite（`output/app.db`）を使います。

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
cd web; npm ci; npm run build; cd ..                                     # 画面を作る（web/dist）
.venv\Scripts\python.exe -m uvicorn api.main:app --port 8000             # 画面 http://localhost:8000 、仕様書 /docs
```

API は `web/dist` があれば画面も配信します。PostgreSQL を使うときは `$env:DATABASE_URL="postgresql+psycopg://user:pass@localhost/estimate"`。画面を直しながら動かすときは `cd web; npm run dev`（http://localhost:5173、API は 8000 に中継）。

お試し用のデータ（架空の顧客、`pdf_data`・`dxf_data` の図面と形状、受注・失注・帳票まで）を入れるときは：

```powershell
.venv\Scripts\python.exe scripts\seed_demo.py --storage output\demo --fresh
$env:ESTIMATE_STORAGE_DIR="output\demo"; $env:PAST_QUOTES_PATH="output\demo\history.csv"; $env:DRAWING_READER="recorded:output\demo\readings.json"
.venv\Scripts\python.exe -m uvicorn api.main:app --port 8000
```

## 画面

左のメニュー（図面を登録／図面一覧／案件・進捗／見積、その下に 図面・見積作業・書類・取引先・設定、最近の見積）と、上の検索欄（図番・品名・書類）、「用語・計算式」のヘルプからなります。詳しくは [docs/03_画面設計書.md](docs/03_画面設計書.md)、使い方は [docs/04_操作マニュアル.md](docs/04_操作マニュアル.md)。

| 画面 | できること |
|---|---|
| 図面を登録 | 図面PDF・STEP・DXFをまとめてドラッグ＆ドロップ。同じ名前のPDFとCADデータを1件にまとめ、図面PDFの読み取り結果を下書きにして、図番・品名・顧客・改訂・分類を確認して登録。同じ図番があれば「新しい版として登録」。CADデータは登録時に解析 |
| 新規見積作成・解析の進み具合 | 登録済みの図面を選び、顧客・数量（必須）・希望納期・材質・表面処理、Kファクター（STEP）や板厚・平板（DXF）を入れて開始。CADデータの解析 → 図面の読み取り → マスタ照合 → 見積計算 → 類似実績の検索 の段階と、途中で分かった値を表示 |
| 見積結果 | 費目（材料・レーザー切断・ピアス・曲げ・段取り・追加加工・表面処理・特急割増・粗利）と単価・小計・消費税・合計。図面から読み取った条件と状態、CADデータ（3D・展開図・確定／概算とその理由）、未入力の項目の一覧（埋まるまで発行できない）、類似実績、修正履歴、チャットでの条件変更、顧客提示モード |
| 案件・進捗 | カンバンと一覧、ステータスのグループのタブ、絞り込み、警告（納期超過・納期まで3日以内・3日以上更新なし・回答待ち3日以上）。ドラッグか ←→ で進捗を変える。失注は理由と他社価格を記録 |
| 図面一覧・図面詳細 | 条件検索・似た形・図面内の文字、分類、プレビュー／リスト。図面詳細は版の切り替え、図面情報（属性の編集）・改訂履歴・使用先・関連書類・注意／不具合メモ、図面への書き込み、似た図面の過去実績 |
| 見積・振り返り分析 | 見積の一覧。直近3・6・12か月の見積件数・受注率・平均回答日数・見積総額、顧客別・担当者別・失注の理由 |
| 帳票発行 | 見積書・納品書・請求書。テンプレートを選んでプレビュー（発行するPDFそのもの）、発行、PDFのダウンロード、発行履歴 |
| 書類・ナレッジ検索、取引先管理 | キーワード検索（文字データのあるPDF・Excel、登録済みの図面・見積）と書類の登録。協力会社・顧客の台帳（記録と表示だけ） |
| 設定 | マスタ（材料・工程・表面処理・価格方針・自社情報）、見積ロジック（計算式の表示だけ）、見積書テンプレート、案件ステータス、分類・属性項目、担当者、過去見積の取り込み（CSVの列の対応づけ） |

ログインはまだありません。「設定 ＞ 担当者」で、この端末で操作する担当者を選ぶと、修正履歴・進捗の変更・帳票の発行に「だれが」が記録されます。

## 解析方式

CadQuery/OpenCASCADEを使用し、FreeCADへの実行時依存はありません。LLMは使いません（比較結果は [analysis/llm_comparison.md](analysis/llm_comparison.md)）。

1. STEPを読み込み、単一の閉じたSolid、有効体積、B-Rep妥当性を確認
2. モデル対角寸法から線形公差を設定し、共有EdgeからFace隣接グラフを構築
3. 対向平面と同軸円筒の候補を面積支持でクラスタリングして板厚を決定し、表裏ペアを対応付け（平面間の距離は解析的に計算。間に材料がない対向面＝ヘムのすき間は除外）
4. 平面―円筒―平面の隣接関係から曲げ軸、角度、内外Rを取得
5. **汎用展開**（`src/sheetmetal_unfold.py`）：最大の平面から、表裏の片側の面（平面と曲げ円筒）だけを隣接グラフでたどる。曲げを通過するたびに、子側を曲げ軸まわりに円筒の角度範囲だけ戻し、曲げ代 `BA = θ(内R + K·t)` だけ平行移動する（折り曲げの厳密な逆変換）。曲げ面上の点は角度に応じて展開する。平板・平行曲げ・トレー・多段フランジ・ヘムを同じ処理で扱う
6. 片側の表面の境界エッジを、共有するB-Rep頂点でつないで展開図の閉ループを作る（多角形の合成をしないため細いすき間が生じない）。面積＝外周ループ−内側ループ、切断長＝境界エッジ長の合計（平面上のエッジは剛体移動なので厳密長、曲げ端は展開後の長さ）、穴数＝内側ループ数
7. **自己検算で status と信頼度を決める**。次のすべてを満たしたときだけ `success`（信頼度 high）
   - 展開面積 ＝ 体積÷板厚 ＋ Σθ·t·(K−0.5)·曲げ長さ（許容差1%）
   - 切断長 ＝ 切断面の総面積÷板厚 ＋ Σ曲げ端ごとの θ·t·(K−0.5)（許容差1%）
   - 全表裏ペアのちょうど片側が展開に含まれる、展開後の平面度、閉路がある場合の経路間の位置ずれ
   - 展開図が単一の単純な外形で、穴は外形の内側にあり互いに重ならない
   - 切断面の連結成分数 ＝ 展開図のループ数、全曲げが展開に含まれる
8. 検算が通らない場合は値を返したうえで `partial`（見積は「概算」）、reason code `FLAT_PATTERN_UNVERIFIED`。曲げ部にかかる穴・切欠きは `INTERNAL_BOUNDARY_ESTIMATED`（`partial`）。展開図自体を作れない場合だけ、体積と切断面積から概算した値を `FLAT_PATTERN_ESTIMATED`（`partial`）で返す

Kファクター未指定（既定0.33）で曲げがある場合は、従来どおり `assumptions` に記録して信頼度 medium（概算）とします。曲げ角度（`bend_evidence.angle_deg`）は円筒面の角度範囲から取り、170°以上をヘムとして報告します。

結果は既存の `thickness_mm`、`blank_area_mm2`、`cut_length_mm`、`hole_count`、`bend_count` に加え、`metric_quality`（検算差を evidence に記載）、`assumptions`、`warnings`、`reason_codes`、2D輪郭座標と曲げ線を含みます。`status` は `success`、`partial`、`unsupported`、`error` です。

評価結果（誤差±10%、`--gate` 合格）：

| データ | Lv0 | Lv1 | Lv2 | Lv3 | Lv4 | 危険誤答 | 平均処理時間 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 開発 golden_v1（508件）[レポート](analysis/golden_eval_latest.md) | 100% | 100% | 100% | 100% | 100% | 0件 | 0.1〜0.4s |
| ホールドアウト holdout_v1（200件）[レポート](analysis/golden_eval_holdout.md) | 100% | 100% | 100% | 100% | 100% | 0件 | 0.1〜0.4s |
| 追加検証 seed=3（1,000件、golden_v2基準）[レポート](analysis/golden_eval_seed3.md) | 100% | 100% | 100% | 100% | 100% | 0件 | 0.1〜0.8s |
| 追加検証 seed=4（1,000件、golden_v2基準）[レポート](analysis/golden_eval_seed4.md) | 100% | 100% | 100% | 100% | 100% | 0件 | 0.1〜0.7s |

（解析成功率。全件が「確定で正解」。面積の最大誤差は0.004%、切断長・板厚の誤差は0）

生成手順そのものを変えたストレステスト（任意姿勢、多角形底面、角度混在、閉じた筒、短い平坦部、直交フランジ、大きいフランジ、多段の12区分・480件）では、危険誤答は0件でした。解析不可は3件、閉じた筒は40件すべてが「概算」になりました。閉じた筒では展開図の検算が合わず、以前は面積がほぼ0や負の値のまま返っていましたが、検算に失敗した展開値が体積÷板厚（面積）・切断面積÷板厚（切断長）から20%以上外れるときは、その概算値を使うようにしました（面積は0や負にならない）。詳細と改善候補は [analysis/stress_data_design.md](analysis/stress_data_design.md)（レポートは [analysis/stress_eval.md](analysis/stress_eval.md)）。

### 展開図DXF（`src/dxf_analyzer.py`）

展開図DXFと、ユーザーが入力した板厚から、展開面積・切断長・穴数・曲げ数を求めます。ezdxf と shapely によるルールベースで、LLMは使いません（ルールベースで受入条件を大きく上回ったため）。

1. **図形の正規化**：ブロック参照（INSERT）を分解し（ブロック内のレイヤ0・BYBLOCKの属性は参照側から継承）、LWPOLYLINE の円弧（bulge）・ARC・CIRCLE を曲線にする。`$INSUNITS` でmmに換算する（単位なしはmmとみなし、概算。mm以外の単位も概算：mmで描いた図面の単位がインチに設定されていると値が25.4倍になり、ファイルの中にはそれを見分ける手がかりがないため）。DIMENSION・TEXT は形状として扱わず、寸法に表示された数値（上書き文字か寸法ブロックの文字）と板厚表記は検算に使う
2. **線の役割**：レイヤ名に意味があれば使う（CUT／外形、BEND／曲げ線、DIM／寸法、FRAME／図枠、CENTER／中心線、MARK／ケガキ など英日の表記ゆれ）。なければ線種で判断し、実線は切断線の候補、実線以外の直線は曲げ線・中心線の候補とする
3. **端点をまとめる**：端点どうしの距離が0.02 mm以内なら1点にまとめる（格子への丸めは使わない）。重複線は重ねて1本にし、重複した曲線どうしの間にできる幅0.05 mm未満の細い面は無視する
4. **部品の特定**：連結した線のまとまりごとに閉じた領域を作る。複数の面に分かれ、ほかの図形を囲み、図を囲む面のほかの面（表題欄の枠）が隅にまとまっているものを図枠・表題欄として除く（実線の曲げ線で帯に分かれた部品は、帯が部品の幅いっぱいに渡るので図枠にしない）。残った閉じた輪郭のうち最も外側を部品の外形、その内側を穴とする。外形と別の色の閉じた線は穴に数えず、別のレイヤの閉じた線は穴に数えたうえで、どちらも概算にする
5. **問題の検出（確定しない）**：外形が閉じない（0.1 mmを超えるすき間）→ `OPEN_CONTOUR`、外形になり得る輪郭が2つ以上 → `MULTIPLE_PARTS`、切断線がない → `NO_CUT_CONTOUR`（いずれも unsupported）
6. **曲げ線**：途中で分割された線をつないだうえで、両端が外形上にあるものを曲げ線とする。穴の中心で交差する線は中心線として除く
7. **自己検算**：次の場合は値を返しつつ `partial`（概算）にする
   - 0.02〜0.1 mm のすき間を閉じた
   - 部品の内側に外形と同じ色の閉じない線がある（別の色ならケガキ線として除外）
   - 役割の分からない破線がある
   - 寸法に表示された数値が外形の大きさと合わない（表示の桁の丸めは許す）
   - 表題欄の板厚表記と入力した板厚が違う
   - 単位がmm以外（`UNIT_NOT_MM`）
   - 部品の内側に外形と別の色・別のレイヤの閉じた線がある
   - 曲げ線が1本もない（`NO_BEND_LINES`。平板と、曲げ線の描き漏れを区別できないため。画面の「曲げなし（平板）」、API の `flat_confirmed` で平板と指定すると確定できる）

評価結果（誤差±10%、`--gate` 合格。X は「確定しない」ことが正解）：

| データ | D0 きれい | D1 注記あり | D2 乱雑 | X 確定しない | 危険誤答 | 平均処理時間 |
|---|---:|---:|---:|---:|---:|---:|
| 開発 dxf_v1（800ファイル）[レポート](analysis/dxf_eval_latest.md) | 100% | 100% | 100% | 100% | 0件 | 0.03〜0.04s |
| ホールドアウト dxf_holdout_v1（400ファイル）[レポート](analysis/dxf_eval_holdout.md) | 100% | 100% | 100% | 100% | 0件 | 0.02〜0.03s |
| 追加検証 seed=23（800ファイル）[レポート](analysis/dxf_eval_seed23.md) | 100% | 100% | 100% | 100% | 0件 | 0.02〜0.03s |

表の D0〜D2 は解析成功（値が正しい割合）です。規則を足す前（ホールドアウトと追加検証の評価時）は全件が「確定で正解」でした。単位が mm 以外（`UNIT_NOT_MM`）と曲げ線がない図面（`NO_BEND_LINES`）を確定にしない規則を足したあとの開発用では、「確定で正解」は D0・D1 80%、D2 58% で、残りは平板とインチ単位の図面の「正解だが概算」です（危険誤答は0件のまま）。面積・切断長の最大誤差は0.005%です。X は全件が解析不可で、理由コードは実際の問題（開いた外形／2部品）と全件一致しました。画面では「図面を登録」で `.dxf` を登録し、見積で板厚を入力すると、展開図と見積が表示されます。

### 図面PDFの加工条件（`src/pdf_reader.py`）

図面PDFから、見積に効く加工条件（材質・板厚・数量・表面処理・追加加工・特急）、補助として図番・改訂、特記事項（厳しい公差・外観・検査の要求）を読み取ります。画面で図面PDFをSTEP／DXFと一緒にアップロードすると、読み取った値が見積条件の入力欄に入ります。板厚だけは形状の値（STEPから測った板厚、DXFは解析に使った板厚）を表示して計算に使い、図面の板厚は照合だけに使います（違えば「確認してください」に出ます）。

**Claude（画像を読める LLM）は「図面に書いてある文字の書き写し」だけに使い、コード・数値・確定かどうかはルールで決めます。**

1. **ページの準備**（pypdfium2）：ページ全体を長辺1568pxで画像化し、スキャン・FAX・手書きの図面とA3は左右半分を2倍で追加する。CAD出力の図面は文字データ（テキスト層）を位置つきで取り出して一緒に渡す
2. **書き写し**：決まった形式（ツール呼び出しのJSONスキーマ）で、各項目の原文（`text`）と書かれている場所、改訂履歴、部品表の行、手書き・押印の注記を返させる。空欄は null、マスターにない表記は「未登録」とし、似たコードに寄せない
3. **ルールで判定**（`src/pdf_terms.py`）：マスターの別名（`aliases`）と表記ゆれの規則で、材質・表面処理・追加加工のコードを決める（例：`2XM4タップ`→`TAP_M4`×2、ただの穴は加工に数えない）。板厚・数量は原文から数値を取り出す。改訂がある場合は最新の改訂の値を使い、部品表の数量は見出し（台分・合計など）で1個あたりに換算する。手書きの「至急」は印字の「通常」より優先する
4. **確定か要確認か**
   - CAD図面：原文がテキスト層に実在しない値は要確認。テキスト層にある加工指示・数量表記が結果にないときは、その行をヒントにもう一度読み、それでも欠ければ要確認
   - スキャン・FAX・手書き：拡大した区画の画像で独立にもう一度読み（2回の呼び出しは並列）、2回の結果が一致した項目だけ確定
   - ルールとLLMの判断が食い違う、改訂の最新値と本文が異なる、原文を解釈できない場合も要確認
5. **見積への反映**（`src/pdf_quote.py`、`src/quote_engine.py`）：項目ごとに「読み取り済み／要確認／未登録／記載なし」を付けて入力欄に入れる。見積結果の画面は、読み取った値を初期値にした見積の条件で計算し、要確認などの状態はヒントとして表示する。見積書を発行した時点で、条件を確定として扱う（概算の見積書はない）。表面処理は `surface_treatments.csv`、追加加工は `process_rates.csv`、特急割増と粗利率は `pricing_policy.csv` で計算する。マスターにない材質・表面処理・追加加工は、画面では図面の原文を名前にした「マスターにない」項目になり、担当者が単価を入れるまで発行できない（API の `/api/documents` だけで使う場合は、今までどおり金額に入れず備考に「別途見積」と書く）。図面の材質がマスターにない・書かれていないときは、材質を選ぶまで金額を出さない（一覧の先頭の材質で計算しない）。API で数量・表面処理・特急を指定すると図面の値に代わり、チャットでの変更も図面の条件に入る。API の `/api/quotes` は確定の項目だけを金額に入れ、`/api/documents` は見積書を作る時点ですべての項目を確定として扱う（数量が決まっていなければ `QUANTITY_REQUIRED`）。図面とチャット・手入力の同じ加工は、手入力の個数で置き換える（二重に数えない）。特急は「特急不要」「特急：なし」「NO URGENT」などの否定を特急なしと読み、手書き・押印が印刷と食い違うときは要確認にする

金額はLLMに計算させません。応答はキャッシュ（`.claude_cache/`）に保存して再利用し、テストはAPIを呼びません。

![図面PDFの読み取り結果と見積（見積結果の画面）](docs/images/screens/05_estimate.png)

評価結果（`--gate`）：

| データ | 価格項目がすべて正しい | 自動確定 | 危険誤答を含む図面 | 特記事項 | 判定 |
|---|---:|---:|---:|---:|---|
| 開発 pdf_v1（300枚）[レポート](analysis/pdf_eval_latest.md) | 99.3% | 92.7% | 0.0% | 98.3% | 合格 |
| ホールドアウト pdf_holdout_v1（200枚）[レポート](analysis/pdf_eval_holdout.md) | 73.0% | 67.0% | 0.0% | 73.0% | **未完了**（下記） |

ホールドアウトは、実行中にClaude APIのクレジット残高が尽き、200枚中52枚が読み取れていません（上の値はこの52枚を不正解として数えたもの）。読み取れた148枚では価格項目の正解率95.3〜100%、自動確定90.5%、危険誤答0件、開発用にない様式（28枚）でも92.9〜100%でした。ただしFAX図面の表面処理が76.7%（値はすべて正しく、要確認になったもの）で、種類別の条件（90%以上）を下回っています。クレジット追加後に同じコマンドを再実行すると、残り52枚だけAPIを呼んで判定を完了できます。方式ごとの精度・危険誤答・時間・トークンの比較は [analysis/pdf_llm_comparison.md](analysis/pdf_llm_comparison.md)。1枚あたりの時間は約6〜7秒、トークンは入力約19,000／出力約900です。

## 対象形状

- 曲げのない一定板厚の平板
- 丸穴、角穴、長穴、任意形状の閉じた穴
- 外周切欠き、スリット
- L・U・Z形状
- 90度以外、異なる内側Rを含む複数の直線曲げ
- 1枚材で、コーナーに明確な切れ目またはリリーフがある開放箱・トレー、フランジ上のフランジ（多段）
- ヘム（オープンヘム、180°曲げ）。展開したうえで170°以上の曲げとして報告

以下は理由付き `unsupported`、または安全に取得できる項目だけを返す対象です。

- ロール、円錐
- 曲げ部にかかる穴・切欠き（値は返すが `partial`）
- 複数Solid、アセンブリ、溶接構造
- 密閉箱、展開時に重なるフランジ（自己検算で検出し `partial`）
- 深絞り、ルーバー、ビード、エンボス、バーリング、自由曲面主体の成形品
- 板厚変化、曲げRを確認できない鋭角形状、不正B-Rep

## 見積

金額はLLMではなく、マスター（`data/materials.csv`、`data/process_rates.csv`、`data/surface_treatments.csv`、`data/pricing_policy.csv`）の単価と率だけで計算します。粗利率は `pricing_policy.csv` の `margin_rate` です。各マスター行の `aliases` は図面の表記ゆれ（例：SPCC-SD、ボンデ鋼板、ユニクロ、PEMナット）で、`MasterLoader.resolve_alias` で照合します。マスターにない材質・処理・加工は似たものに寄せず「未登録」として扱います。

- 材料：展開面積 × 板厚 × 密度 × 材料単価 × 歩留まり係数
- 切断：切断長 × レーザー切断単価
- 穴：穴数 × ピアス単価
- 曲げ：曲げ数 × 曲げ単価
- 段取り・追加工程：登録単価

各マスター行の `charge_scope` が `per_part` なら部品数量倍、`per_order` なら案件につき1回です。

**見積書を発行できる条件**（`src/services/platform/pricing.py`。判定はAPIが行い、未入力があれば発行を拒否します）：金額に必要な項目がすべて入力されていること。数量、材質、形状の値5つ（板厚・展開面積・切断長・穴数・曲げ数。形状解析で求められなかったときは担当者が入力）、マスターにない材料・加工・表面処理の単価（材料はkg単価と密度、加工は1か所あたり、表面処理は1個あたり）。入力されていれば、図面の読み取りが「要確認」でも、形状解析が「概算」でも、その値で確定として発行できます（要確認・概算はヒントとして表示）。表面処理「なし」・特急「なし」は入力済みとして扱います。

**マスターにない材料・加工・表面処理**：似たものに寄せません。担当者が入れた単価をその見積だけに使い、マスターには登録しません（材料は入力したkg単価に、マスターの板材と同じ歩留まり係数1.15を掛けます）。金額は入力した単価でも `QuoteEngine` がルールで計算します。**希望納期**は記録・見積書への表示・進捗の警告に使い、金額には影響しません（特急は人が指定します）。

## 見積書の出力

画面の「帳票発行」（見積結果の「見積書を発行」から開けます）で、案件とテンプレートを選び、プレビュー（発行するPDFそのもの）を確かめて発行します。未入力の項目がある見積は発行できず（APIが 409 `NOT_ISSUABLE` と未入力の一覧を返します）、見積結果へのリンクを出します。見積書は発行した時点で条件が確定したものとして扱い、つねに「御見積書」として出します（概算の見積書はありません）。番号は案件番号（`Q-2026-0001`。再発行は `-2`、`-3`…）。LLM API は使いません。コードは `src/quote_document.py`（中身と金額）、`src/quote_pdf.py`（PDFの描画。テンプレートの表示項目に対応）、`src/services/platform/documents.py`（発行・番号・記録）です。

**納品書・請求書**（`src/trade_pdf.py`）：受注した案件で、発行済みの見積書と同じ金額で作ります（番号 `D-2026-0001`、`I-2026-0001`）。請求書は適格請求書として、自社の登録番号と税率別の合計（10%対象の金額・消費税・合計）を入れます。支払期限は翌月末です。

**テンプレート**（設定 ＞ 見積書テンプレート）：表示する項目（費目の内訳＝項目名と数量だけ・単価と数量・図番と改訂・有効期限・備考欄・社印）を切り替えます。適用する顧客を指定でき、原価・粗利は社外用に出しません（固定）。

API だけで使う場合の見積書（`POST /api/documents`、番号は `Q20260928-001` 形式で `output/quote_log.csv` に記録）も今までどおり使えます。

**書式**：A4縦1ページ。見本は [analysis/quote_document_sample.pdf](analysis/quote_document_sample.pdf)（確定）と [analysis/quote_document_sample_estimate.pdf](analysis/quote_document_sample_estimate.pdf)（概算）。概算の見本は、今は使いません。この機能で出力した例は [通常](analysis/quote_document_example.pdf)・[未登録の加工を別途見積にした例](analysis/quote_document_example_separate.pdf)・[社内用の見積根拠](analysis/quote_document_example_internal.pdf) です（仮の宛先。`python scripts/make_quote_examples.py` で作り直せます）。

![出力した見積書の例（通常と、未登録の加工を別途見積にした例）](analysis/quote_document_example.png)

**記載項目**

| 区分 | 項目 | 値の出どころ |
|---|---|---|
| 見出し | タイトル | 「御見積書」（作成した時点で条件は確定） |
| | 見積番号・発行日・有効期限 | 番号は `Q`＋発行日＋`-`＋その日の連番3桁（`Q20260928-001`）。有効期限は発行日＋`quote_validity_days` |
| 宛先 | 会社名（御中）、部署・担当者名（様） | 画面で入力（会社名は必須） |
| 自社 | 会社名・住所・TEL/FAX・登録番号・担当者、押印欄 | `data/company.csv`（押印欄は枠だけ） |
| 取引条件 | 件名・納期・受渡場所・取引条件・有効期限 | 件名は既定「{図番} {品名} 製作」、納期は「受注後 N営業日」（通常 `lead_time_days_normal`、特急 `lead_time_days_rush`）、受渡場所は既定「貴社指定場所」、取引条件は `company.csv` の支払条件 |
| 明細 | No・品名/図番（改訂）・仕様・数量・単位・単価・金額 | 仕様の1行目は「材質 板厚 表面処理」、2行目は「レーザー切断・曲げn箇所・追加加工と個数」。データは複数行に対応（画面は1部品） |
| 合計 | 小計・消費税（税率）・合計、御見積金額（税込） | 下の「金額の決め方」 |
| 備考 | 見積の根拠・定型文・特急・特記事項・自由記述 | 図面の図番と改訂・形状ファイル名、`company.csv` の定型文、特急のときは「特急対応」、図面に検査成績書などの要求があれば「別途」、画面で入力した備考 |
| 別途見積 | マスター未登録の追加加工 | 備考に「追加加工「（図面の原文）」はマスター未登録のため、本見積に含めず別途見積とします。」 |

社外用の見積書には、原価の内訳（材料費・切断費など）と粗利率を出しません。「社内用の内訳も出力する」にチェックすると、別のPDF「見積根拠（社内用）」（社外秘）も出力します。中身は原価の各行、小計、特急割増、粗利率、最終価格、解析値と解析の状態、図面から読み取った条件とそれぞれの状態です。

**金額の決め方**（画面の表示も同じ値です）

- 単価 ＝ 見積計算（`QuoteEngine`）の最終価格 ÷ 数量。1円未満は切り上げ
- 金額 ＝ 単価 × 数量、小計 ＝ 金額の合計
- 消費税 ＝ 小計 × `tax_rate`（1円未満は切り捨て）、合計 ＝ 小計 ＋ 消費税

以前の画面は、最終価格を10円単位で切り上げた値を税の区別なく表示していました。今は見積書と同じ「単価・小計（税抜）・消費税・合計（税込）」を表示します。`QuoteEngine` の計算結果は変えていません。

**自社情報の設定**：`data/company.csv` は仮の値です（リポジトリは公開のため、実在の会社名・住所・登録番号は入れません）。実際の自社情報は、同じ形式の `data/company.local.csv`（Git管理外）に書くと、そちらが優先されます。`remark_1`、`remark_2`… が備考の定型文、`delivery_place` が受渡場所の既定値です。税率・有効期限・納期は `data/pricing_policy.csv`（`tax_rate`、`quote_validity_days`、`lead_time_days_normal`、`lead_time_days_rush`）で設定します。

**記録**：出力するたびに `output/quote_log.csv`（Git管理外）に、見積番号、発行日時、宛先、件名、図番、数量、合計、概算かどうか（今はつねに0。以前の記録との互換のため列を残す）、入力ファイル名を1行残します。見積番号の連番はこの記録から決めます（同じ日に何度出力しても重複しません）。

**日本語フォント**：IPAゴシック（`fonts/ipag.ttf`、IPAフォントライセンスv1.0、[ライセンス文](fonts/IPA_Font_License_Agreement_v1.0.txt)）をPDFに埋め込むため、Windows でも文字化けしません。長い会社名・品名・仕様は縮小し、それでも入らなければ折り返して枠内に収めます。同じ入力・同じ発行日時なら同じPDFになります。

## 類似見積

**条件**（`src/similar_quotes.py` の `is_similar`。図面一覧の「似た形」、図面詳細、見積結果の「類似実績」で同じ）：**材料が同じ、曲げ数の差が1以内、穴数の差が2以内**。差の小さい順（曲げと穴の差の合計、曲げの差、穴の差、新しい順）に並べます。曲げ数・穴数が分からない図面（形状ファイルがない）や過去見積は対象外です。対象は、登録済みの図面（最新の版の材質と形状解析の曲げ数・穴数、最新の見積の単価と状態）と、登録図面に結び付かない過去見積の履歴です。今回の単価は自動では書き換えません。LLM API は使いません。

過去見積には、似ている理由・今回との違い・値段の比較（今のマスターで計算し直した標準単価と出し値の比）を付けます（API `POST /api/similar-quotes`）。

**参考価格の効果**（[analysis/similar_quotes_eval.md](analysis/similar_quotes_eval.md)）：種データのうち標準単価を計算できる1,045件で、それより前の見積だけを過去として検索し、実際の単価との誤差を比べました。条件を上の規則に統一したため、以前（材質の系統と板厚で探す方式：全体7.9%、リピート4.9%）より全体の誤差は少し大きくなりましたが、自動見積だけよりは小さいままです。

| 対象 | 自動見積（標準単価）だけ | 類似見積を使う |
|---|---:|---:|
| 全体（1,045件） | 平均誤差 10.8% | 8.8% |
| リピート（270件） | 9.9% | 5.0% |

検索は、種データ（2,084件）で平均 0.5 ms・最大 1.2 ms、1万件に増やした履歴で平均 1.4 ms・最大 3.1 ms でした。

**データの持ち方**：履歴は `data/past_quotes/history.csv`（UTF-8、1行1見積、Gitで差分が読める）に置き、コミットします。材質・表面処理・追加加工は、書かれた文字（`*_text`）と、マスターの別名で対応づけたコード（`*_code`）の両方を持ちます。対応できないものは、系統だけ分かる材質（「SUS」「AL」など）は `material_code` が空で `material_family` だけ、マスターにない材質・処理・加工は `UNREGISTERED`、表面処理の空欄は「記録なし」（空）です。取り込んだ元の行は `original` 列（JSON）に残します。

**過去見積の取り込み**：Excel などから書き出したCSV（UTF-8 か Shift_JIS）を、画面の「見積作業 ＞ 過去見積の取り込み」で取り込みます（データベースに入ります）。列は名前で自動で対応づけ（「顧客名／得意先」「見積日／日付」など）、違うところは画面で直します。コマンドで履歴ファイルに取り込むときは `--map` で列を指定します。欠けた列は空欄になります。同じ見積（見積番号・見積日・顧客が同じ）は二重に入りません。

```powershell
.venv\Scripts\python.exe scripts\import_past_quotes.py data\past_quotes\past_quotes_seed.csv
.venv\Scripts\python.exe scripts\import_past_quotes.py 旧見積.csv --map customer=得意先コード名 --map unit_price=見積単価
.venv\Scripts\python.exe scripts\evaluate_similar_quotes.py --report analysis\similar_quotes_eval.md
```

種データ（`data/past_quotes/past_quotes_seed.csv`、架空）は取り込み済みです。`出典` 列は試験用のため、取り込み時に捨てています（検索・表示には使いません）。

**履歴への追加と結果の記録**：`data/past_quotes/history.csv` は初期データで、最初の起動でデータベースに入ります。以後、見積書を発行するとその見積が履歴（データベース）に入り、案件の受注・失注に合わせて結果も変わります（振り返り分析・類似実績に反映）。コミットされた履歴ファイルは書き換えません。テストは履歴の一時的な複製を使います。

## API（サーバー）

画面は API（FastAPI）だけを呼び、金額・発行の可否・類似の判定はすべて API が決めます（画面で計算し直しません）。設計は [analysis/api_design.md](analysis/api_design.md)、仕様書は起動後の `/docs` にあります。

| 機能 | 呼び出し |
|---|---|
| 図面の登録・一覧・詳細・類似 | `POST /api/drawings/register`、`GET /api/drawings`、`GET/PUT /api/drawings/{id}`、`GET /api/drawings/{id}/similar` |
| 見積（作成は受付番号方式） | `POST /api/estimates` → `GET /api/jobs/{job_id}` → `GET/PUT /api/estimates/{id}`、`POST /api/estimates/{id}/chat` |
| 帳票 | `GET /api/estimates/{id}/documents/preview.png`、`POST /api/estimates/{id}/documents`（kind: quote / delivery / invoice）、`GET /api/issued` |
| 案件・振り返り | `GET /api/cases`、`PUT /api/cases/{id}/status`、`GET /api/review` |
| 検索・書類・取引先 | `GET /api/search`、`GET/POST /api/library`、`GET/POST/PUT /api/partners` |
| 設定・マスター | `/api/statuses`、`/api/categories`、`/api/attributes`、`/api/templates`、`/api/staff`、`/api/master-tables/{table}`、`GET /api/logic` |
| 形状の解析・図面PDFの読み取り（単体） | `POST /api/files` → `POST /api/analyses` または `POST /api/drawings/readings` → `GET /api/jobs/{job_id}` |
| 見積の計算・見積書・類似見積・履歴（画面を使わない呼び出し） | `POST /api/quotes`、`POST /api/documents`、`POST /api/similar-quotes`、`/api/history...` |

更新は版（`version`）つきで受け付け、ほかの人が先に保存していたら 409（`CONFLICT`）を返します（黙って上書きしません）。解析と図面の読み取りは受付番号を返して裏で処理し、受付の状態はファイルに残るので、APIを再起動しても取れます。

| 変数 | 既定 | 内容 |
|---|---|---|
| `DATABASE_URL` | なし（SQLite `<保存先>/app.db`） | データベース。PostgreSQL は `postgresql+psycopg://user:pass@host/db` |
| `ESTIMATE_DATA_DIR` | `data` | 初期データのマスターと自社情報（評価とテストはこの CSV を直接読む） |
| `ESTIMATE_STORAGE_DIR` | `output` | 図面PDF・形状ファイル・帳票・書類・受付・3D表示のファイル |
| `PAST_QUOTES_PATH` | `data/past_quotes/history.csv` | 初期データの見積履歴 |
| `QUOTE_LOG_PATH` | `output/quote_log.csv` | `POST /api/documents` の見積書の記録 |
| `API_TOKEN` | なし | 設定すると `Authorization: Bearer <token>`（または `X-API-Token`）が必要。守りは `api/security.py` の1か所 |
| `MAX_UPLOAD_MB` | 50 | アップロードの上限（種類と中身の先頭も確認） |
| `JOB_WORKERS` | 2 | 裏で処理する数 |
| `DRAWING_READER` | なし | `recorded:<file.json>` で記録済みの読み取り結果を返す（お試し・資料の撮影用。API を呼ばない） |
| `WEB_DIST` | `web/dist` | API が配信する画面 |

## データベース

SQLAlchemy 2 のモデル（`src/db/models.py`）と Alembic のマイグレーション（`migrations/`）で管理します。保管するもの：図面と版（ファイル・解析・読み取り・属性）、書き込みとメモ、案件・ステータスの履歴（当時の名前で残る）、見積（条件・解析・計算結果・修正履歴・チャット）、発行した帳票（金額）、過去見積の履歴、取引先、書類（文字データ）、担当者、案件ステータス、分類、属性項目、テンプレート、マスター、自社情報、連番。構造を変えるときは `alembic revision --autogenerate -m "..."` で移行を作り、`alembic upgrade head`（API の起動時にも実行）で反映します。

## チャットと情報保護

チャットは材料、数量、登録済み追加工程を構造化するだけで、変更後の金額はルールエンジンが再計算します。入力欄は見積の条件のすぐ下にあります。

- `LLM_MODE=mock`：決定論的Mock
- `LLM_MODE=openai`：OpenAI API。キーがなければ設定エラー
- `LLM_MODE=auto`：有効なキーがあればOpenAI、なければ起動時からMock

OpenAI API呼び出しが失敗してもMockへ切り替えず、現在の条件と見積を保持してエラーを表示します。

OpenAIへ送信するのは、チャット文章、現在の材料・数量・追加工程条件、利用可能な材料コード・工程コードだけです。STEP、3D/2D画像、形状座標、寸法、面積、単価、見積金額は送信しません。3Dメッシュと2D輪郭はローカルで生成します。

ただし、解析ロジックへのLLM活用を検証する Claude API（後述）では、形状由来の解析情報（面・寸法・座標の要約など）を送信することがあります（2026-09-27 承認済み）。チャット機能の送信範囲は上記のまま変わりません。図面PDFをアップロードした場合は、加工条件の読み取りのため、図面の画像と文字データを Claude API へ送信します。

`.env`、`.env.local`、`secrets.toml`、秘密鍵形式はGit除外されています。プッシュ前に次を実行します。

```powershell
.venv\Scripts\python.exe scripts\check_secrets.py --history
```

## Claude API（解析へのLLM活用の検証）

`.env` に `ANTHROPIC_API_KEY` を書くだけで使えます（雛形は `.env.example`）。疎通確認とLLM版解析器の評価：

```powershell
.venv\Scripts\python.exe scripts\check_claude_api.py
.venv\Scripts\python.exe scripts\evaluate_golden.py --data golden_data --tolerance 0.10 --analyzer src.llm_only_analyzer:LLMOnlyAnalyzer --limit 20 --workers 4
```

`LLMOnlyAnalyzer`（`src/llm_only_analyzer.py`）は、ルールベースとの比較用の「LLM単独」解析器です。STEPから読んだソリッドの体積・表面積・外寸とB-Rep面の一覧（種類、面積、法線・軸、半径、角度範囲、隣接面）だけをClaudeに渡し、板厚・展開面積・切断長・穴数・曲げ（角度・内R）をすべてClaudeに求めさせます。ルールベースの解析器は呼びません。

幾何計算で検算していない値なので、結果は常に `partial`（見積は「概算」、reason code `LLM_ONLY_UNVERIFIED`、信頼度 low）です。金額は従来どおりマスターとルールで計算します。既定の解析器には採用していません。比較の数値は [analysis/llm_comparison.md](analysis/llm_comparison.md)。

同じ問い合わせの応答は `.claude_cache/` に保存して再利用します（`CLAUDE_CACHE_MODE`）。テストはAPIを呼びません。

## テスト

```powershell
.venv\Scripts\python.exe -m pytest -q
```

テストには、不正入力、B-Rep、適応公差、面隣接、板厚、平板と複数穴、L/U/Z相当の複数曲げ、45/60/120度曲げ、異なるR、2D包含、Kファクター、対象外形状、課金単位、LLM送信項目、API失敗時の状態保持、画面のAPI（金額の一致、発行できる条件、類似の条件、再起動後のデータ、同時保存）が含まれます。`tests/test_e2e_flow.py` はブラウザ（Playwright）で、図面の登録から見積・発行・進捗・受注・納品書・請求書・振り返りまでを1本で通します（`web/dist` がないときは飛ばします。先に `cd web; npm ci; npm run build`）。テストは SQLite を使い、PostgreSQL は要りません。

## ゴールデンデータによる評価

実部品のGolden Dataが入手できるまでの代替として、正解値付きの板金部品を生成して解析ロジックを定量評価します。複雑度をLv0（平板）〜Lv3（多段フランジ）、Lv4（ヘム）に分けて、レベル別の正解率と危険誤答率を出します。

```powershell
.venv\Scripts\python.exe scripts\generate_golden.py --per-level 100 --seed 1 --out golden_data
.venv\Scripts\python.exe scripts\evaluate_golden.py --data golden_data --verify-frozen --tolerance 0.10 --gate --report analysis\golden_eval_latest.md
```

解析ロジック改善の作業指示と受入条件は [analysis/handoff_analysis_logic.md](analysis/handoff_analysis_logic.md) にあります。設計・レベル定義・限界は [analysis/golden_data_design.md](analysis/golden_data_design.md)、最新の結果は [analysis/golden_eval_latest.md](analysis/golden_eval_latest.md)（開発）と [analysis/golden_eval_holdout.md](analysis/golden_eval_holdout.md)（ホールドアウト）、改善前は [analysis/golden_eval_baseline_10pct.md](analysis/golden_eval_baseline_10pct.md) を参照してください。

穴数の正解値の不具合（曲げリリーフの切欠きが穴と数えられることがある）を直した `golden_v2` を追加しました（`--truth-version v2`、[golden/truth_v2.py](golden/truth_v2.py)）。`golden_v1` は変更していません。seed=1 では v1 と v2 の値は同一です。

## 展開図DXFのゴールデンデータ

展開図DXF（平らに広げた輪郭の図面）の解析を評価するデータセットです。同じ部品を、きれいな図面（D0）、注記入りの図面（D1）、レイヤが整理されていない乱雑な図面（D2）、確定してはいけない問題のある図面（X：外周が開いている・2部品）の4通りに描き分けています。板厚はDXFに含まれないため、入力として渡します。

```powershell
.venv\Scripts\python.exe scripts\generate_dxf_golden.py --per-level 40 --seed 21 --out dxf_data
.venv\Scripts\python.exe scripts\evaluate_dxf.py --data dxf_data --analyzer src.dxf_analyzer:DxfAnalyzer --verify-frozen --gate --report analysis\dxf_eval_latest.md
```

設計と生成器の検証は [analysis/dxf_golden_design.md](analysis/dxf_golden_design.md)、解析ロジック開発の受入条件は [analysis/handoff_dxf.md](analysis/handoff_dxf.md)、素朴なベースラインの結果は [analysis/dxf_eval_baseline.md](analysis/dxf_eval_baseline.md)、解析器の方式と結果は上の「解析方式」を参照してください。

## 図面PDFのゴールデンデータ

開発用の図面PDF 300枚と、同じ部品のSTEP 300個は `pdf_data/` にあります（正解は `pdf_data/index.json`）。図面PDFから見積に効く加工条件（材質・板厚・数量・表面処理・追加加工・特急、補助として図番・改訂、特記事項として公差・外観・検査）を読み取るロジックを評価するデータセットです。CAD出力（文字データあり）50%、スキャン20%、FAX20%、手書き・押印入り10%で、書く項目・書く場所・表記ゆれ・改訂・手書きの訂正を変えています。正解は改訂後の最新値、書かれていない項目は「記載なし」、マスターにないものは「未登録」です。

```powershell
.venv\Scripts\python.exe scripts\generate_pdf_golden.py --per-level 60 --seed 31 --out pdf_data
.venv\Scripts\python.exe scripts\evaluate_pdf.py --data pdf_data --reader golden.pdf_naive_baseline:NaiveTextReader --verify-frozen --report analysis\pdf_eval_baseline.md
.venv\Scripts\python.exe scripts\evaluate_pdf.py --data pdf_data --reader src.pdf_reader:PdfConditionReader --verify-frozen --gate --report analysis\pdf_eval_latest.md
```

設計は [analysis/pdf_golden_design.md](analysis/pdf_golden_design.md)（[例](analysis/pdf_samples.png)）、読み取りロジック開発の受入条件は [analysis/handoff_pdf.md](analysis/handoff_pdf.md)、素朴なベースラインの結果は [analysis/pdf_eval_baseline.md](analysis/pdf_eval_baseline.md)、読み取りロジックの方式と結果は上の「図面PDFの加工条件」を参照してください。

## 現時点の評価制約

テスト形状はCadQueryでパラメトリック生成し、理論値と比較しています。実部品のGolden Dataはまだありません。そのため、カバレッジ90%、10%以内正解率95%、危険誤答率1%未満は開発目標であり、現時点の達成値としては主張しません。実データ入手後に形状分類ごとの母数、正解値、誤差分布を記録して評価します。
