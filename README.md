# STEP板金解析・見積デモ

STEP/STP形式の一定板厚板金をローカル解析し、形状根拠、3D形状、展開後2D輪郭、ルールベース見積を同じ画面に表示するデモです。対象外形状に推定値を強制せず、項目別の方式・信頼度・警告・理由コードを返します。

## 起動

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m streamlit run app.py
```

ブラウザでSTEPを選び、必要ならKファクターを変更して「解析を実行」を押します。既定値0.33のまま曲げ形状を解析した場合、加工条件未確認の概算として表示されます。

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

（解析成功率。全件が「確定で正解」。面積の最大誤差は0.004%、切断長・板厚の誤差は0）

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

金額はLLMではなく、`data/materials.csv` と `data/process_rates.csv` の単価だけで計算します。

- 材料：展開面積 × 板厚 × 密度 × 材料単価 × 歩留まり係数
- 切断：切断長 × レーザー切断単価
- 穴：穴数 × ピアス単価
- 曲げ：曲げ数 × 曲げ単価
- 段取り・追加工程：登録単価

各マスター行の `charge_scope` が `per_part` なら部品数量倍、`per_order` なら案件につき1回です。必須解析値が欠けた場合は金額を出しません。概算解析値を含む場合は見積も概算と表示します。

## チャットと情報保護

チャットは材料、数量、登録済み追加工程を構造化するだけで、変更後の金額はルールエンジンが再計算します。

- `LLM_MODE=mock`：決定論的Mock
- `LLM_MODE=openai`：OpenAI API。キーがなければ設定エラー
- `LLM_MODE=auto`：有効なキーがあればOpenAI、なければ起動時からMock

画面に実際のモードを表示します。OpenAI API呼び出しが失敗してもMockへ切り替えず、現在の条件と見積を保持してエラーを表示します。

OpenAIへ送信するのは、チャット文章、現在の材料・数量・追加工程条件、利用可能な材料コード・工程コードだけです。STEP、3D/2D画像、形状座標、寸法、面積、単価、見積金額は送信しません。3Dメッシュと2D輪郭はローカルで生成します。

ただし、解析ロジックへのLLM活用を検証する Claude API（後述）では、形状由来の解析情報（面・寸法・座標の要約など）を送信することがあります（2026-09-27 承認済み）。チャット機能の送信範囲は上記のまま変わりません。

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

テストには、不正入力、B-Rep、適応公差、面隣接、板厚、平板と複数穴、L/U/Z相当の複数曲げ、45/60/120度曲げ、異なるR、2D包含、Kファクター、対象外形状、課金単位、LLM送信項目、API失敗時の状態保持、Streamlit表示が含まれます。

## ゴールデンデータによる評価

実部品のGolden Dataが入手できるまでの代替として、正解値付きの板金部品を生成して解析ロジックを定量評価します。複雑度をLv0（平板）〜Lv3（多段フランジ）、Lv4（ヘム）に分けて、レベル別の正解率と危険誤答率を出します。

```powershell
.venv\Scripts\python.exe scripts\generate_golden.py --per-level 100 --seed 1 --out golden_data
.venv\Scripts\python.exe scripts\evaluate_golden.py --data golden_data --verify-frozen --tolerance 0.10 --gate --report analysis\golden_eval_latest.md
```

解析ロジック改善の作業指示と受入条件は [analysis/handoff_analysis_logic.md](analysis/handoff_analysis_logic.md) にあります。設計・レベル定義・限界は [analysis/golden_data_design.md](analysis/golden_data_design.md)、最新の結果は [analysis/golden_eval_latest.md](analysis/golden_eval_latest.md)（開発）と [analysis/golden_eval_holdout.md](analysis/golden_eval_holdout.md)（ホールドアウト）、改善前は [analysis/golden_eval_baseline_10pct.md](analysis/golden_eval_baseline_10pct.md) を参照してください。

穴数の正解値の不具合（曲げリリーフの切欠きが穴と数えられることがある）を直した `golden_v2` を追加しました（`--truth-version v2`、[golden/truth_v2.py](golden/truth_v2.py)）。`golden_v1` は変更していません。seed=1 では v1 と v2 の値は同一です。

## 現時点の評価制約

テスト形状はCadQueryでパラメトリック生成し、理論値と比較しています。実部品のGolden Dataはまだありません。そのため、カバレッジ90%、10%以内正解率95%、危険誤答率1%未満は開発目標であり、現時点の達成値としては主張しません。実データ入手後に形状分類ごとの母数、正解値、誤差分布を記録して評価します。
