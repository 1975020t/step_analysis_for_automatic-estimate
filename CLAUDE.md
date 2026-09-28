# CLAUDE.md

STEP形式の板金部品を解析し、ルールベースで見積を出すシステムのバックエンド（デモUIはStreamlit）。
現在の主タスクは **過去の類似見積の参照**（このPhaseではLLM APIを使わない）。最終ゴールと受入条件は [analysis/handoff_similar_quotes.md](analysis/handoff_similar_quotes.md) にある。着手前に読むこと。
見積書PDFの出力（[analysis/handoff_quote_document.md](analysis/handoff_quote_document.md)）は実装済み。図面PDF読み取りの懸念点への対応（[analysis/handoff_pdf_followup.md](analysis/handoff_pdf_followup.md)）はAPIが使えるようになってから別に行う。今回は図面PDFの読み取りのコードを変えない。
STEPの解析（[analysis/handoff_analysis_logic.md](analysis/handoff_analysis_logic.md)）と展開図DXFの解析（[analysis/handoff_dxf.md](analysis/handoff_dxf.md)）は完了済み。壊さないこと。
進め方・設計は任されている。途中で確認を取らずに、受入条件を満たすまで進めてよい。

## 環境

- Python 3.11 で動作確認済み（CadQuery の都合で 3.10〜3.12）。依存は `requirements.txt`
- ユーザーは Windows / PowerShell。README のコマンドは `.venv\Scripts\python.exe` 形式
- Claude APIキーは環境変数 `ANALYSIS_ANTHROPIC_API_KEY`（クラウド環境に設定済み。`ANTHROPIC_API_KEY` や `.env` でも可）
- 秘密情報は `.env`（Git管理外、雛形は `.env.example`）。プッシュ前に `python scripts/check_secrets.py --history`

## データ

- 開発用のデータはリポジトリにファイルとして置いてある（生成し直す必要はない）：`golden_data`（STEP）、`stress_data`、`dxf_data`、`pdf_data`、`pdf_shift_dev`。生成コマンドで作り直しても同じファイルになる（`golden/file_normalize.py`）。評価の出力 `*/results.csv` はGit管理外
- **ホールドアウトのデータは置かない**（`golden_holdout`、`dxf_holdout`、`pdf_holdout`、STEP の seed=3・4、DXF の seed=23、図面PDFの seed=33、判定用の語彙ずらし）。最終判定のときだけ生成する
- 過去見積の種データは `data/past_quotes/`

## よく使うコマンド

```
python -m pytest -q                                                      # 全テスト（全件パスが前提）
python scripts/generate_golden.py --per-level 100 --seed 1 --out golden_data     # 開発用データ508件（約1分）
python scripts/evaluate_golden.py --data golden_data --verify-frozen --tolerance 0.10 --gate \
    --report analysis/golden_eval_latest.md                              # 評価（約2分）。--gate で合否
python scripts/evaluate_golden.py --data golden_data --tolerance 0.10 --levels Lv2 --limit 20   # 部分的な素早い確認
python scripts/render_golden.py --data golden_data --per-level 3          # 形状と判定の一覧画像
python scripts/generate_stress.py --per-level 40 --seed 21 --out stress_data   # 作り方を変えたストレステスト用データ（analysis/stress_data_design.md）
python scripts/check_claude_api.py                                        # Claude APIキーの疎通確認
python scripts/generate_dxf_golden.py --per-level 40 --seed 21 --out dxf_data        # DXF開発用データ800件（約2分）
python scripts/evaluate_dxf.py --data dxf_data --analyzer src.dxf_analyzer:DxfAnalyzer --verify-frozen --gate \
    --report analysis/dxf_eval_latest.md                                 # DXF評価。結果は dxf_data/results.csv
python scripts/generate_pdf_golden.py --per-level 60 --seed 31 --out pdf_data        # 図面PDF開発用300枚。pdf_data/ にPDFとSTEPをコミット済み（作り直しても同一）
python scripts/evaluate_pdf.py --data pdf_data --reader src.pdf_reader:PdfConditionReader --verify-frozen --gate \
    --report analysis/pdf_eval_latest.md                                 # 図面PDF評価。結果は pdf_data/results.csv
python scripts/generate_pdf_golden.py --per-level 20 --seed 41 --vocab golden/vocab/shift_dev.json --out pdf_shift_dev   # 語彙をずらした100枚
python scripts/evaluate_pdf.py --data pdf_shift_dev --reader src.pdf_reader:PdfConditionReader \
    --verify-frozen golden/datasets/pdf_shift_dev_v1.json --gate --gate-profile safety     # 知らない語への安全性
```

部品ごとの結果は `golden_data/results.csv`（誤差・理由コード・展開方式・LLMトークン数）。失敗の分析はまずここから。

## コード構成

- `src/sheetmetal_analyzer.py` 解析の司令塔。出力は `src/models.py` の `SheetMetalAnalysis`
- `src/sheetmetal_recognition.py` 板厚・表裏ペア・曲げの認識
- `src/sheetmetal_unfold.py` 展開
- `src/sheetmetal_geometry.py` 公差、面隣接グラフ、2Dループ処理
- `src/quote_engine.py` 見積計算。`is_estimate`（概算表示）は解析結果の status / 信頼度から決まる
- `src/claude_api.py` Claude APIクライアント（応答キャッシュ、トークン集計）
- `src/llm_only_analyzer.py` LLM単独の解析器（比較用。結果は常に概算。`--analyzer src.llm_only_analyzer:LLMOnlyAnalyzer` で評価できる）
- `golden/` ゴールデンデータ生成器、`golden/datasets/golden_v1.json` が開発用の凍結正解値（508件）、`holdout_v1.json` が最終判定用（200件、開発中は開かない）
- `scripts/evaluate_golden.py` 評価ハーネス（STEP）、`scripts/evaluate_dxf.py`（DXF）
- `golden/dxf_golden.py` 展開図DXFの生成器（変種 D0/D1/D2/X）。`golden/datasets/dxf_v1.json` が開発用、`dxf_holdout_v1.json` が最終判定用（開発中は開かない）
- `golden/dxf_naive_baseline.py` DXFの素朴なベースライン（比較用。本番コードではない）
- `golden/pdf_golden.py` 図面PDFの生成器（描画は `golden/pdf_render.py`）。`golden/datasets/pdf_v1.json` が開発用、`pdf_holdout_v1.json` が最終判定用。`golden/pdf_unseen.py` はホールドアウト専用の様式（開発中は開かない）
- `golden/pdf_naive_baseline.py` 図面PDFの素朴なベースライン（比較用）
- `golden/vocab/shift_dev.json` 語彙をずらした図面の語彙（開発用）。`golden/datasets/pdf_shift_dev_v1.json` が正解。判定用の語彙ずらしはリポジトリにない
- `src/pdf_reader.py`・`src/pdf_terms.py`・`src/pdf_quote.py` 図面PDFの読み取り、語の解釈ルール、見積への反映
- `data/` マスター：材料・工程（追加加工を含む）・表面処理・価格方針（粗利率、特急割増）。各行の `aliases` が表記ゆれ。照合は `MasterLoader.resolve_alias`
- `src/quote_document.py`・`src/quote_pdf.py`・`src/quote_log.py` 見積書PDF（中身と金額の端数処理、reportlab での描画、見積番号と `output/quote_log.csv`）。自社情報は `data/company.csv`（`data/company.local.csv` を優先）、フォントは `fonts/ipag.ttf`。例は `python scripts/make_quote_examples.py`
- `src/past_quotes.py`・`src/similar_quotes.py` 見積履歴（`data/past_quotes/history.csv`、コミットする）と類似見積の検索。取り込みは `scripts/import_past_quotes.py`、効果の評価は `scripts/evaluate_similar_quotes.py`（`analysis/similar_quotes_eval.md`）。テストは履歴の一時コピーを使う（`tests/conftest.py`）
- `src/dxf_analyzer.py` 展開図DXFの解析器 `DxfAnalyzer`（ルールベース。板厚は入力。方式は README の「解析方式」）

## 守ること

1. **正解データを変えて合格させない。** `golden/sheetgen.py`・`golden/sampler.py`・`golden/dxf_golden.py`・`golden/pdf_golden.py`・`golden/pdf_render.py`・`golden/pdf_unseen.py`・`golden/truth_v2.py`・`golden/datasets/` は変更しない。マスターの行を増やして合格させない（別名の追加はよい）。生成器の不具合を見つけたら `golden_v2` を新設し、`golden_v1` は残して報告する
2. **ホールドアウトで調整しない。** 正解値 `golden/datasets/holdout_v1.json`・`dxf_holdout_v1.json`・`pdf_holdout_v1.json`・`pdf_holdout_s33_v1.json` と `golden/pdf_unseen.py` は開かない。seed=2（STEP）・seed=22（DXF）・seed=32 と 33（図面PDF）のデータは最終判定のときだけ生成する。ホールドアウトの評価には `--hide-examples` を付ける。なお STEP の holdout_v1・DXF の dxf_holdout_v1・図面PDFの pdf_holdout_v1 は評価を終え、過去見積の種データ（`data/past_quotes/`）に使ったので、今後の最終判定には使わない（STEP は seed=3・4、DXF は seed=23、図面PDFは seed=33 などの別データを使う）
3. **金額をLLMに計算させない。** 金額はマスターCSVとルールで決定論的に計算する
4. **LLMの出力をそのまま数値に使わない。** 数値を出す場合は幾何計算で検算する。形状データをClaude APIへ送ることは承認済み
5. **pytest からAPIを呼ばない。** 記録済みの応答か偽のクライアントを使う
6. `SheetMetalAnalysis` の既存フィールドと `status` の意味を壊さない（UIと見積が依存している）
7. 変えたら `pytest` と評価ハーネスを両方回し、レポートの数字で改善を示す。数字は推測で書かない
