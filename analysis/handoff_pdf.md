# 引き継ぎ：図面PDFの加工条件の読み取り

## 最終ゴール

板金部品の **図面PDF**（CAD出力・スキャン・FAX・手書きの入ったもの）から、見積の金額に影響する加工条件（材質・板厚・数量・表面処理・追加加工・特急）を読み取り、マスターのコードに対応づけて見積に反映する。**読み取りに自信のない項目、マスターにない項目、図面に書かれていない項目は確定させず**、人の確認や入力に回す。形状の値（展開面積・切断長など）はこれまでどおり STEP または DXF の解析から取り、PDFの条件と組み合わせて見積を出す。

達成方法（アルゴリズム、LLMの使い方、コード構成、作業順序）は任せる。途中の確認は不要。

## 受入条件

| 項目 | 条件 |
|---|---|
| 正解率 | 価格項目（材質・板厚・数量・表面処理・追加加工・特急）の **それぞれで95%以上**。PDFの種類（ベクター・スキャン・FAX・手書き）ごとにも、各項目 **90%以上** |
| 危険誤答 | 誤った値を要確認にせず返したもの。価格項目ごとに **1%以下**、1つでも含む図面は **2%以下** |
| 自動確定 | 価格項目がすべて正しく、要確認が1つもない図面が **75%以上**（要確認を付けすぎない） |
| 補助・特記事項 | 図番・改訂がそれぞれ90%以上、特記事項（公差・外観・検査）が90%以上 |
| 汎化 | ホールドアウト `pdf_holdout_v1`（200枚）でも上の条件をすべて満たす。そのうち開発用にない様式の図面だけでも、価格項目それぞれ90%以上 |
| 見積への反映 | 読み取った条件が見積に入る：表面処理（`data/surface_treatments.csv`）、追加加工（`data/process_rates.csv`）、特急の割増と粗利率（`data/pricing_policy.csv`）。未登録・要確認・記載なしの項目は金額に入れず、見積は「概算」になり、理由が画面に出る |
| UI | Streamlit 画面で図面PDFと、STEP または DXF をアップロードすると、読み取った条件が入力欄に入り、要確認・未登録・記載なしの項目が一目で分かる。ユーザーが直してから見積を確定できる |
| 性能・費用 | 1枚あたり平均30秒以内（LLMの待ち時間込み）。評価レポートにトークン数を残す |
| 回帰 | `python -m pytest -q` がすべてパスし、STEP の評価（`scripts/evaluate_golden.py --gate`）と DXF の評価（`scripts/evaluate_dxf.py --gate`）も合格のまま |

開発用の図面PDFとSTEPは `pdf_data/` にコミット済み（生成コマンドで作り直しても同じファイルになる）。

判定コマンド（受入条件のうち数値のものは `--gate` で判定される）:

```
python scripts/generate_pdf_golden.py --per-level 60 --seed 31 --out pdf_data
python scripts/evaluate_pdf.py --data pdf_data --reader src.pdf_reader:PdfConditionReader --verify-frozen --gate --report analysis/pdf_eval_latest.md
```

最終判定（開発の最後に一度だけ）:

```
python scripts/generate_pdf_golden.py --per-level 40 --seed 32 --unseen-ratio 0.2 --out pdf_holdout
python scripts/evaluate_pdf.py --data pdf_holdout --reader src.pdf_reader:PdfConditionReader --verify-frozen golden/datasets/pdf_holdout_v1.json --gate --report analysis/pdf_eval_holdout.md
```

部分的な確認には `--kinds fax`、`--levels Lv2`、`--names Lv0_0018`、`--limit 30` が使える。図面ごとの結果（読み取り値・正解・書かれていた場所）は `pdf_data/results.csv`。

## 正解の規則

データの詳細は [pdf_golden_design.md](pdf_golden_design.md)。要点:

- 改訂後の **最新の値** が正解（改訂表、二重線による手書きの訂正、手書きの追記を反映。「M4タップ 2ヶ所追加」は既存の数に足す）
- **書かれていない項目は `null`**（推測しない）。表面処理は「なし」「生地」と明記されていれば `NONE`、何も書かれていなければ `null`
- **マスターにないものは `UNREGISTERED`**。似たコードに寄せない（SUS430→SUS304、バーリングタップ→M4タップ、圧入スペーサ→圧入スタッド は誤り）
- 追加加工は **1個あたりの数**。分けて指示されていれば合計、部品表が「○台分」「合計」なら部品数量で割る。単なる穴・キリ穴・抜きは追加加工ではない
- 特記事項に普通公差・定型注記（バリなきこと等）・塗装の色や艶の指定は含めない

## インターフェース

- `src/pdf_reader.py` に `PdfConditionReader` を作る（引数なしで生成できること。名前を変える場合はコマンドと資料も更新）
- `.read(path) -> dict`。形式は `scripts/evaluate_pdf.py` の冒頭に書いてある。主なキー：`material`、`thickness_mm`、`quantity`、`surface_treatment`、`processes`（`code` と `count_per_part`）、`rush`、`flags`、`drawing_no`、`revision`、`needs_review`（人の確認が必要な項目名のリスト）、`usage`（任意：LLMの呼び出し回数・トークン数）
- コードはマスターのもの。マスターの読み込みと表記ゆれの照合には `src/master_loader.py` の `MasterLoader`（`resolve_alias`、`normalize_term`、定数 `UNREGISTERED`）が使える
- 見積の条件（`src/models.py` の `QuoteCondition`）に表面処理・特急などを足すのはよい。既存のフィールドと、`SheetMetalAnalysis` の意味は壊さない

## 現状（参考）

開発用300枚での結果（詳細は [pdf_golden_design.md](pdf_golden_design.md) の「参考」）。

| 読み取り方 | 価格項目すべて正しい | 自動確定 | 危険誤答を含む図面 | 最も低い項目 |
|---|---:|---:|---:|---|
| テキスト層＋キーワード（`golden/pdf_naive_baseline.py`） | 5.3% | 5.3% | 94.7% | 追加加工 34.0% |
| Claude 1回呼び出しの試作（リポジトリには含めていない） | 89.3% | 47.0% | 4.7% | 追加加工 92.3% |

試作は受入条件のうち「追加加工の正解率と危険誤答」「危険誤答を含む図面」「自動確定」を満たしていない。主な誤りはマスターの似た加工への寄せ、タップの下穴をタップと数える、手書きの追加を足さない、要確認の付けすぎ、出力形式の崩れ。

## 推奨する方向（任意。より良い方法があれば変えてよい）

1. **画像とテキスト層の両方を使う**。ベクターPDFでは、LLMが読んだ数値や記号がテキスト層に実在するかを照合できる。A3やFAXは表題欄・注記・改訂表・部品表を切り出して拡大すると読みやすい
2. **マスターへの対応づけはLLMに任せきりにしない**。読んだ文字列を `resolve_alias` で照合し、合わないものはLLMの判断を「候補＋根拠」として受け取り、規則で確認する。自信がなければ `UNREGISTERED` か要確認
3. **要確認は、自信のない項目だけに付ける**。例えば、切り出し方や解像度を変えた2回の読み取りが一致しない項目、テキスト層と食い違う項目、改訂や手書きが関わる項目だけを要確認にする。付けすぎは自動確定の条件で落ちる
4. **出力形式の崩れに備える**（スキーマ検証と再試行）

## LLM（Claude API）の利用

使ってよい。キーは環境変数 `ANALYSIS_ANTHROPIC_API_KEY`（クラウド環境に設定済み。`ANTHROPIC_API_KEY` や `.env` でも可）、疎通確認は `python scripts/check_claude_api.py`。`src/claude_api.py` の `ClaudeClient` は今は文字列の入力だけなので、画像を送るなら拡張してよい（応答キャッシュのキーには画像の内容を含めること）。

- 評価は同じ図面を何度も回すので、応答キャッシュ（record モード）を使うと2回目以降は費用がかからない。試作では300枚で入力約168万トークン・出力約24万トークンだった
- **金額はLLMに計算させない**。金額はマスターとルールで決める
- LLMが読んだ数値（数量・板厚・個数）は、そのまま確定値にしない。テキスト層との照合、2回読みの一致などで確かめ、確かめられないものは要確認にする
- pytest からAPIを呼ばない（記録済みの応答か偽のクライアントを使う）
- LLMを使った場合は、使わない方法（または別の使い方）との比較（正解率・危険誤答・処理時間・トークン数）を残す

## 制約

- **正解データを変えて合格させない**：`golden/pdf_golden.py`、`golden/pdf_render.py`、`golden/pdf_unseen.py`、`golden/sampler.py`、`golden/sheetgen.py`、`golden/datasets/` は変更しない。生成器の不具合を見つけたら新しい版（例：`pdf_v2`）を作り、既存の版は残して報告する
- **ホールドアウトで調整しない**：`golden/datasets/pdf_holdout_v1.json` と `golden/pdf_unseen.py`（ホールドアウトだけで使う様式）は開かない。seed=32 のデータは最終判定のときだけ生成する
- **マスターの行（コード）を増やして合格させない**。SUS430 などは「未登録」が正解。表記ゆれの別名を `aliases` に足すのはよい
- STEP と DXF の解析を壊さない
- 評価レポートの数字はハーネスの出力をそのまま使い、推測で書かない

## 完了時に残すもの

- 改修したコードとテスト
- `analysis/pdf_eval_latest.md`（開発データ）と `analysis/pdf_eval_holdout.md`（ホールドアウト）
- README への図面PDFの読み取り方式と結果の追記、画面の例（画像）
- LLMを使った場合は、その比較結果

## 対象外（今回はやらない）

- 図面からの形状・寸法の読み取り、STEP・DXFとの突き合わせ
- 溶接記号・表面性状記号・幾何公差の枠の解釈（今回のデータでは文字で書かれている）
- 1枚に複数部品が描かれた図面、組立図
- 注文書やメールからの数量の取得
- 実際の顧客図面での評価（実データの入手後に行う）
