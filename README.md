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

CadQuery/OpenCASCADEを使用し、FreeCADへの実行時依存はありません。STEPの解析にLLMは使いません。

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

生成手順そのものを変えたストレステスト（任意姿勢、多角形底面、角度混在、閉じた筒、短い平坦部、直交フランジ、大きいフランジ、多段の12区分・480件）では、危険誤答は0件でした。解析不可は3件、閉じた筒は40件すべてが「概算」になりました。詳細と改善候補は [analysis/stress_data_design.md](analysis/stress_data_design.md)（レポートは [analysis/stress_eval.md](analysis/stress_eval.md)）。

### 展開図DXF（`src/dxf_analyzer.py`）

展開図DXFと、ユーザーが入力した板厚から、展開面積・切断長・穴数・曲げ数を求めます。ezdxf と shapely によるルールベースで、LLMは使いません（ルールベースで受入条件を大きく上回ったため）。

1. **図形の正規化**：ブロック参照（INSERT）を分解し（ブロック内のレイヤ0・BYBLOCKの属性は参照側から継承）、LWPOLYLINE の円弧（bulge）・ARC・CIRCLE を曲線にする。`$INSUNITS` でmmに換算する（単位なしはmmとみなし、概算）。DIMENSION・TEXT は形状として扱わず、寸法値と板厚表記は検算に使う
2. **線の役割**：レイヤ名に意味があれば使う（CUT／外形、BEND／曲げ線、DIM／寸法、FRAME／図枠、CENTER／中心線、MARK／ケガキ など英日の表記ゆれ）。なければ線種で判断し、実線は切断線の候補、実線以外の直線は曲げ線・中心線の候補とする
3. **端点をまとめる**：端点どうしの距離が0.02 mm以内なら1点にまとめる（格子への丸めは使わない）。重複線は重ねて1本にし、重複した曲線どうしの間にできる幅0.05 mm未満の細い面は無視する
4. **部品の特定**：連結した線のまとまりごとに閉じた領域を作る。複数の面に分かれ、ほかの図形を囲むまとまりを図枠・表題欄として除く。残った閉じた輪郭のうち最も外側を部品の外形、その内側を穴とする
5. **問題の検出（確定しない）**：外形が閉じない（0.1 mmを超えるすき間）→ `OPEN_CONTOUR`、外形になり得る輪郭が2つ以上 → `MULTIPLE_PARTS`、切断線がない → `NO_CUT_CONTOUR`（いずれも unsupported）
6. **曲げ線**：途中で分割された線をつないだうえで、両端が外形上にあるものを曲げ線とする。穴の中心で交差する線は中心線として除く
7. **自己検算**：次の場合は値を返しつつ `partial`（概算）にする
   - 0.02〜0.1 mm のすき間を閉じた
   - 部品の内側に外形と同じ色の閉じない線がある（別の色ならケガキ線として除外）
   - 役割の分からない破線がある
   - 寸法（DIMENSION の実測値）が外形の大きさと合わない
   - 表題欄の板厚表記と入力した板厚が違う

評価結果（誤差±10%、`--gate` 合格。X は「確定しない」ことが正解）：

| データ | D0 きれい | D1 注記あり | D2 乱雑 | X 確定しない | 危険誤答 | 平均処理時間 |
|---|---:|---:|---:|---:|---:|---:|
| 開発 dxf_v1（800ファイル）[レポート](analysis/dxf_eval_latest.md) | 100% | 100% | 100% | 100% | 0件 | 0.02〜0.03s |
| ホールドアウト dxf_holdout_v1（400ファイル）[レポート](analysis/dxf_eval_holdout.md) | 100% | 100% | 100% | 100% | 0件 | 0.02〜0.03s |
| 追加検証 seed=23（800ファイル）[レポート](analysis/dxf_eval_seed23.md) | 100% | 100% | 100% | 100% | 0件 | 0.02〜0.03s |

D0〜D2 は全件が「確定で正解」でした。面積・切断長の最大誤差は0.005%です。X は全件が解析不可で、理由コードは実際の問題（開いた外形／2部品）と全件一致しました。画面では `.dxf` をアップロードし、板厚を入力すると、展開図と見積が表示されます（[画面例](analysis/dxf_ui.png)）。

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

## Claude API

キーは環境変数 `ANALYSIS_ANTHROPIC_API_KEY`（または `ANTHROPIC_API_KEY`、`.env` でも可。雛形は `.env.example`）。疎通確認：

```powershell
.venv\Scripts\python.exe scripts\check_claude_api.py
```

同じ問い合わせの応答は `.claude_cache/` に保存して再利用します（`CLAUDE_CACHE_MODE`）。テストはAPIを呼びません。

STEP解析へのLLM活用の実験（LLM単独の解析器 `LLMOnlyAnalyzer` と比較の記録）は、本流の解析ロジックではないため `archive/llm-step-analysis` ブランチに分けています。

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

## 展開図DXFのゴールデンデータ

展開図DXF（平らに広げた輪郭の図面）の解析を評価するデータセットです。同じ部品を、きれいな図面（D0）、注記入りの図面（D1）、レイヤが整理されていない乱雑な図面（D2）、確定してはいけない問題のある図面（X：外周が開いている・2部品）の4通りに描き分けています。板厚はDXFに含まれないため、入力として渡します。

```powershell
.venv\Scripts\python.exe scripts\generate_dxf_golden.py --per-level 40 --seed 21 --out dxf_data
.venv\Scripts\python.exe scripts\evaluate_dxf.py --data dxf_data --analyzer src.dxf_analyzer:DxfAnalyzer --verify-frozen --gate --report analysis\dxf_eval_latest.md
```

設計と生成器の検証は [analysis/dxf_golden_design.md](analysis/dxf_golden_design.md)、解析ロジック開発の受入条件は [analysis/handoff_dxf.md](analysis/handoff_dxf.md)、素朴なベースラインの結果は [analysis/dxf_eval_baseline.md](analysis/dxf_eval_baseline.md)、解析器の方式と結果は上の「解析方式」を参照してください。

## 現時点の評価制約

テスト形状はCadQueryでパラメトリック生成し、理論値と比較しています。実部品のGolden Dataはまだありません。そのため、カバレッジ90%、10%以内正解率95%、危険誤答率1%未満は開発目標であり、現時点の達成値としては主張しません。実データ入手後に形状分類ごとの母数、正解値、誤差分布を記録して評価します。
