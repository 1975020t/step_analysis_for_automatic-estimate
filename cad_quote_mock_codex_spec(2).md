# CAD自動見積＋対話補正モック 実装仕様書

## 1. 目的

製造業向けの自動見積システムのモックを構築する。

ユーザーが3D CAD（STEPファイル）をアップロードすると、CADから取得できる幾何情報を解析し、CSVに定義した単価・見積ルールを用いて初期見積を自動作成する。

その後、ユーザーとLLMがチャット形式で会話し、CADデータだけでは判断できない追加工程・特殊加工・数量変更などを補足しながら、見積条件を構造化して更新する。

最終的に、以下の体験を実現する。

```text
STEPファイルをアップロード
        ↓
CADから特徴量を自動抽出
        ↓
CSVマスタを参照して初期見積
        ↓
ユーザーとAIがチャット
        ↓
追加工程・条件変更を構造化
        ↓
単価マスタを参照して再計算
        ↓
最終見積を表示
```

このモックでは「AIが価格を推測する」のではなく、LLMはユーザーの自然言語を見積条件へ変換する役割に限定する。

金額計算は必ず決定論的なPythonロジックで行う。

---

# 2. モックの対象範囲

## 2.0 最重要方針：データを先に解析してから題材を確定する

**Codexは、実装開始時点で「CNC切削部品」「穴加工」「ポケット加工」などのデモシナリオを固定してはならない。**

最初に公開STEPデータを実際に取得・読み込みし、Pythonでどの情報を安定して抽出できるかを確認する。その解析結果を根拠として、見積デモで扱う製造シーン、材料、工程、CSVマスタ、見積ロジックを確定すること。

実装の最初のマイルストーンはアプリ画面ではなく、**「STEPデータから実際に取得できる情報の棚卸し」**とする。

進め方は以下を必須とする。

1. NIST等の公開STEP候補を1〜3ファイル取得する
2. CadQuery / OpenCASCADEで各ファイルを読み込む
3. 抽出可能なGeometry・属性を一覧化する
4. 穴・ポケット・溝などのManufacturing Featureをどこまで安定して認識できるか検証する
5. 抽出結果を `analysis/cad_data_inventory.md` または同等のMarkdownに記録する
6. 取得できる情報と見積要素（材料費・加工工数）の対応関係を整理する
7. その結果を見て、最も説明しやすく実装可能なデモシナリオを決定する
8. シナリオ確定後にCSVマスタと見積ロジックを実装する
9. その後にStreamlit UIとLLMチャット補正を実装する

### データ解析時に最低限確認する項目

以下は「必ず取得できる」と仮定せず、実データに対して取得可否を確認すること。

| 分類 | 確認項目 | 見積への利用候補 |
|---|---|---|
| 基本Geometry | Bounding Box / X,Y,Z | 素材寸法、材料量 |
| 基本Geometry | Volume | 製品重量、材料費 |
| 基本Geometry | Surface Area | 表面処理、複雑度補助 |
| Topology | Face数 / Edge数 | 複雑度補助 |
| Feature | 円筒面 / 穴候補 | 穴加工工数 |
| Feature | ポケット候補 | 切削加工工数 |
| Feature | 溝候補 | 切削加工工数 |
| Feature | 除去体積を推定可能か | 切削量・加工時間 |
| PMI/属性 | 材質 | 材料費 |
| PMI/属性 | 寸法・公差 | 加工難易度補正 |
| PMI/属性 | 表面粗さ | 仕上げ工数補正 |

### シナリオ選定ルール

データ解析後、次の基準で題材を選ぶこと。

- STEPから取れる情報が、材料費または加工工数の推定に明確につながる
- 見積ロジックをユーザーへ説明できる
- 推定根拠を画面上で明示できる
- 取得精度が不安定なFeatureをデモの中核にしない
- CSVによるシミュレーション単価を自然に適用できる
- 第2フェーズのチャット補正で「CADだけでは分からない条件」を追加できる余地がある

たとえば、実データから穴を安定して認識できるなら「穴加工」を採用する。穴認識が不安定で、VolumeとBounding Boxのみが安定しているなら、材料費＋除去量ベースの簡易切削費にシナリオを変更する。PMIから公差が取得できるなら、公差による加工難易度補正を追加してよい。

**重要：仕様書内の後続シナリオは現時点の仮説であり、実データ解析結果を優先して変更してよい。**

## 2.1 暫定対象

現時点ではCNC切削部品を第一候補とするが、上記のデータ解析結果に応じて変更可能とする。

対象CAD形式：

- STEP / STP

材料候補：

- AL5052
- SUS304
- SS400

工程候補：

- 材料費
- 基本切削加工
- 穴加工
- 段取り
- 研磨
- バフ仕上げ
- 皿穴加工
- 表面処理
- その他追加工程

これらは**データ解析後に採否を確定する候補**であり、すべてを実装する必要はない。

## 2.2 対象外

初期モックでは以下は実装しない。

- 実CAMによるツールパス生成
- 厳密な加工時間シミュレーション
- 図面PDFのOCR / VLM解析
- 複雑な公差解析
- ERP連携
- 実在企業の価格データ
- 完全自動見積
- 過去実績を用いた機械学習

## 2.3 デモで使用する公開CADデータ

### 採用データ

初期デモでは、**NIST（米国国立標準技術研究所）が公開している MBE PMI Validation and Conformance Testing Project の Simplified Test Case（STC）に含まれる STEP AP242 データ**を使用する。

第一候補は **STC-06** とする。NISTはFTC/STC向けのSTEP AP242ファイルを公開しており、STCはFTC 6〜10を簡略化したテストケースで、対応するFTCと同じGeometryを持つ。NIST公式ページでは、これらのテストケース、CADモデル、STEPファイルは制限なく利用できると明記されている。

入手元：

- NIST: `Download Free CAD Models, STEP Files, and Test Results`
- STEP AP242ファイル一式をダウンロードし、STC-06のSTEP/STPファイルを `samples/nist_stc06.step` として配置する。

採用理由：

- 公的機関が配布しており、デモ利用条件が明確
- STEP AP242なのでPython/CadQuery/OpenCASCADEから解析できる
- 単純な直方体だけではなく、製造部品らしいGeometryを持つ
- 将来的に寸法・PMI等を扱う拡張にもつなげやすい
- 特定企業や顧客の機密CADを使わずにデモできる

### 補助データ

穴Feature Recognitionの検証を追加したい場合は、NISTが2024年に公開した **Hole Test Case（HTC）** も候補とする。HTCはthrough hole、depth hole、counterbore、countersinkなど異なる穴タイプをテストするためのデータである。初期モックでは必須にせず、STC-06で基本動作を完成させた後の追加検証に使用する。

### データ選定時のルール

STC-06をローカルで読み込んだ結果、初期デモに必要な穴・切削Featureが少なすぎる場合は、同じNIST STEPパッケージ内の **STC-07〜STC-10** から、以下を満たす部品を1つ選んで差し替えてよい。

- 単一部品である
- 3軸CNC切削部品として説明しやすい
- Bounding Box、Volume、Surface Areaに差が出る形状である
- 円筒面または穴候補を1つ以上持つことが望ましい
- 極端に複雑ではなく、デモ時に形状を説明できる

選定した実ファイル名はREADMEに明記する。

---

## 2.4 デモのシチュエーション（暫定）

> **この節は固定仕様ではない。2.0のデータ解析結果を先に確認し、より適切な題材がある場合はCodexがこのシナリオを書き換えてよい。**
> シナリオを変更した場合は、READMEに「取得できたデータ」「採用した見積ロジック」「変更理由」を記録すること。

### 想定ユーザー

中小のCNC切削加工会社で見積を担当している営業・製造担当者。

顧客から新規部品のSTEPファイルが届き、**「この部品をAL5052で10個作る場合、いくらで見積を出すか」**を判断する場面を想定する。

### デモストーリー

#### Step 1: 顧客からCADを受領

顧客からNIST公開データを顧客部品に見立てた `nist_stc06.step` を受け取ったものとする。

ユーザーはStreamlit画面で以下を指定する。

```text
CAD: nist_stc06.step
材料: AL5052
数量: 10個
```

#### Step 2: CADから初期情報を自動取得

Python/CadQueryでSTEPを解析し、少なくとも以下を自動取得して画面に表示する。

```text
外形寸法      XXX × XXX × XXX mm
体積          XXX mm³
表面積        XXX mm²
面数          XX
エッジ数      XX
推定穴数      X
推定穴径      φXX mm ...
```

ここで表示する数値はハードコードせず、**実際のNIST STEPファイルから実行時に取得すること**。

#### Step 3: CSVマスタから初期見積を自動生成

取得したGeometryと `materials.csv` / `process_rates.csv` / `complexity_rules.csv` を使い、次のような明細を自動計算する。

```text
材料費
基本切削加工費
穴加工費
段取り費
----------------
初期原価
利益率
初期見積価格
```

ここまでが **Phase 1: CADベース自動見積**。

#### Step 4: CADだけでは分からない条件をチャットで追加

次に見積担当者が実際の加工条件を確認した結果、CAD Geometryだけでは考慮されていない作業があるというシナリオにする。

ユーザー入力例：

```text
穴のうち2箇所は皿もみにしてください。
仕上げはバフも必要です。
```

LLMは「バフ」がマスタ上のどの工程か一意に決まらないため、次のように確認する。

```text
「バフ仕上げ」は「#400バフ仕上げ」と「鏡面バフ仕上げ」のどちらを意味しますか？
```

ユーザー：

```text
#400です。
```

LLMは発言を以下の構造化操作へ変換する。

```json
{
  "operations": [
    {"action": "add", "process_code": "COUNTERSINK", "quantity": 2, "unit": "hole"},
    {"action": "add", "process_code": "BUFF_400", "quantity": 1, "unit": "job"}
  ]
}
```

#### Step 5: 再見積

Quote EngineがCSVマスタを再参照し、追加工程を含めて再計算する。

画面では初期見積との差分を明示する。

```text
初期見積       ¥XX,XXX

追加：皿穴加工   +¥1,200
追加：#400バフ   +¥3,500
利益率反映        +¥X,XXX
---------------------------
最終見積       ¥XX,XXX
```

### デモで伝えたい価値

このデモでは「CADから完全自動で正しい見積を出せる」と主張しない。以下の2段階の価値を示す。

1. **CADから機械的に取得できる情報は自動で取り込み、標準条件の初期見積を数秒で作れる**
2. **CADだけでは判断できない現場固有の作業は、人間とLLMの会話で補足し、企業の単価マスタに基づいて安全に再計算できる**

つまり、デモの主題は **「完全自動見積」ではなく「自動初期見積＋対話型補正」** とする。

### デモでやらないこと

- NIST部品の実際の製造原価を再現すること
- CAMと同等の加工時間を算出すること
- NISTデータに実際の材料・加工単価が含まれていると仮定すること
- LLM自身に金額を推測させること

NISTから使用するのはGeometry/STEPデータのみであり、価格・加工時間・単価は本モック用のシミュレーションデータとしてCSVに定義する。

---

# 3. 技術スタック

推奨構成：

- Python 3.11+
- Streamlit
- CadQuery
- pandas
- Pydantic
- OpenAI API またはLLM呼び出しを抽象化したインターフェース
- CSV

必要に応じて：

- scikit-learn（将来の類似案件検索用）
- pytest

LLMプロバイダーは差し替え可能にする。

---

# 4. システムアーキテクチャ

```text
┌─────────────────────────────┐
│         Streamlit UI         │
│                             │
│ CADアップロード             │
│ 材料・数量入力              │
│ 初期見積表示                │
│ チャットUI                   │
│ 見積明細表示                │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│       Application Layer      │
│                             │
│ QuoteService                │
│ ChatQuoteService            │
└──────────┬─────────┬────────┘
           │         │
           ▼         ▼
┌────────────────┐  ┌────────────────────┐
│ CAD Analyzer   │  │ LLM Interpreter    │
│ CadQuery       │  │ 自然言語→構造化JSON│
└───────┬────────┘  └─────────┬──────────┘
        │                     │
        ▼                     ▼
┌──────────────────────────────────────┐
│           Quote Engine               │
│                                      │
│ Geometry Features                    │
│ + Quote Conditions                   │
│ + CSV Master                         │
│ → Deterministic Calculation          │
└──────────────────┬───────────────────┘
                   │
                   ▼
          ┌──────────────────┐
          │ CSV Master Data  │
          └──────────────────┘
```

---

# 5. ディレクトリ構成

```text
cad-quote-mock/
│
├── app.py
├── requirements.txt
├── README.md
├── .env.example
│
├── src/
│   ├── __init__.py
│   ├── cad_analyzer.py
│   ├── quote_engine.py
│   ├── quote_service.py
│   ├── chat_service.py
│   ├── llm_client.py
│   ├── models.py
│   └── master_loader.py
│
├── data/
│   ├── materials.csv
│   ├── process_rates.csv
│   ├── complexity_rules.csv
│   └── historical_quotes.csv
│
├── samples/
│   └── nist_stc06.step
│
└── tests/
    ├── test_cad_analyzer.py
    ├── test_quote_engine.py
    └── test_chat_service.py
```

---

# 6. CAD解析仕様

## 6.1 入力

STEPファイル。

## 6.2 出力

以下の構造体を返す。

```json
{
  "bbox_x_mm": 100.0,
  "bbox_y_mm": 80.0,
  "bbox_z_mm": 20.0,
  "volume_mm3": 120000.0,
  "surface_area_mm2": 22000.0,
  "face_count": 18,
  "edge_count": 42,
  "vertex_count": 24,
  "estimated_hole_count": 4,
  "estimated_hole_diameters_mm": [10.0, 10.0, 10.0, 10.0]
}
```

## 6.3 最低限取得するGeometry

CadQueryで以下を取得する。

- Bounding Box X/Y/Z
- Volume
- Surface Area
- Face数
- Edge数
- Vertex数

## 6.4 穴認識

モックでは簡易実装でよい。

円筒面を検出し、円筒面の半径・軸方向などから穴候補を抽出する。

完全なFeature Recognitionは不要。

誤検出があり得るため、UI上では「推定穴数」と表示する。

---

# 7. 見積条件モデル

Pydanticで以下を定義する。

```python
class QuoteCondition(BaseModel):
    material: str
    quantity: int = 1
    additional_processes: list[AdditionalProcess] = []
    notes: list[str] = []


class AdditionalProcess(BaseModel):
    process_code: str
    quantity: float = 1
    unit: str = "job"
    user_text: str | None = None
    confirmed: bool = True
```

例：

```json
{
  "material": "SUS304",
  "quantity": 10,
  "additional_processes": [
    {
      "process_code": "COUNTERSINK",
      "quantity": 2,
      "unit": "hole",
      "user_text": "穴2つ皿もみ",
      "confirmed": true
    },
    {
      "process_code": "BUFF_400",
      "quantity": 1,
      "unit": "job",
      "user_text": "#400のバフ仕上げ",
      "confirmed": true
    }
  ]
}
```

---

# 8. CSVマスタ仕様

## 8.1 materials.csv

```csv
material,density_kg_m3,price_per_kg,waste_factor
AL5052,2680,900,1.15
SUS304,7930,700,1.20
SS400,7850,180,1.15
```

`waste_factor` は材料取り代・スクラップを疑似的に表現する。

---

## 8.2 process_rates.csv

```csv
process_code,display_name,calculation_type,unit_price,unit
BASE_MILLING,基本切削加工,per_minute,120,minute
DRILLING,穴加工,per_hole,250,hole
SETUP,段取り,flat,3000,job
COUNTERSINK,皿穴加工,per_hole,600,hole
POLISH,研磨,flat,2500,job
BUFF_400,#400バフ仕上げ,flat,3500,job
BUFF_MIRROR,鏡面バフ仕上げ,flat,6000,job
SURFACE_TREATMENT,表面処理,flat,5000,job
```

---

## 8.3 complexity_rules.csv

```csv
rule_code,min_faces,max_faces,multiplier
SIMPLE,0,10,1.0
MEDIUM,11,30,1.2
COMPLEX,31,999999,1.5
```

---

## 8.4 historical_quotes.csv

初期モックでは見積計算には必須ではないが、将来の類似案件表示用に持つ。

```csv
part_id,material,bbox_x_mm,bbox_y_mm,bbox_z_mm,volume_mm3,face_count,hole_count,quantity,final_price
P001,AL5052,100,80,20,120000,18,4,10,12100
P002,AL5052,105,75,20,125000,20,4,10,12500
P003,SUS304,100,80,20,120000,18,4,10,16800
```

---

# 9. 初期見積ロジック

## 9.1 材料費

CADの体積から重量を計算する。

```text
volume_m3 = volume_mm3 / 1_000_000_000
weight_kg = volume_m3 × density_kg_m3
material_cost = weight_kg × price_per_kg × waste_factor × quantity
```

モックのため厳密な素材ブランク形状は考えず、CAD体積をベースにする。

将来的にはBounding Boxベースの素材取りに変更可能。

---

## 9.2 基本加工時間

疑似式を使用する。

```text
base_minutes = 10
volume_factor = volume_mm3 / 10000 × 0.5
face_factor = face_count × 0.3
hole_factor = estimated_hole_count × 2

estimated_minutes =
    (base_minutes + volume_factor + face_factor + hole_factor)
    × complexity_multiplier
```

この式に製造上の正確性は求めない。

重要なのは「Geometryから工数が算出され、その結果が見積に反映される」というデモ体験である。

---

## 9.3 基本加工費

```text
milling_cost = estimated_minutes × BASE_MILLING.unit_price × quantity
```

---

## 9.4 穴加工

```text
drilling_cost = estimated_hole_count × DRILLING.unit_price × quantity
```

---

## 9.5 段取り

```text
setup_cost = SETUP.unit_price
```

数量に関係なく1案件1回とする。

---

## 9.6 追加工程

チャットから追加された工程を `process_rates.csv` にマッピングする。

例：

```text
COUNTERSINK quantity=2
→ 2 × 600 = 1,200円

BUFF_400 quantity=1
→ 1 × 3,500 = 3,500円
```

---

## 9.7 原価・見積価格

```text
subtotal_cost =
    material_cost
    + milling_cost
    + drilling_cost
    + setup_cost
    + additional_process_cost

margin_rate = 0.25

quote_price = subtotal_cost × (1 + margin_rate)
```

UIでは10円または100円単位で丸める。

---

# 10. 見積結果モデル

```python
class QuoteLine(BaseModel):
    code: str
    name: str
    quantity: float
    unit_price: float
    amount: float
    source: str


class QuoteResult(BaseModel):
    lines: list[QuoteLine]
    subtotal_cost: float
    margin_rate: float
    final_price: float
```

`source` は以下を設定する。

- `cad`
- `master`
- `chat`
- `user`

これにより、「どの情報がCAD由来で、どれが会話で追加されたか」を追えるようにする。

---

# 11. チャット機能

## 11.1 LLMの役割

LLMには見積金額を計算させない。

LLMの役割は以下のみ。

1. ユーザー発言を解析
2. 見積マスタのどの項目に対応するか判定
3. 数量・単位を抽出
4. 不明な場合は確認質問を生成
5. 見積条件の追加・変更・削除命令をJSONで返す

---

## 11.2 ユーザー入力例

```text
焼け取りとバフが必要。
穴のうち2つは皿もみにして。
```

LLM出力例：

```json
{
  "status": "needs_confirmation",
  "operations": [
    {
      "action": "add",
      "process_code": "COUNTERSINK",
      "quantity": 2,
      "unit": "hole"
    }
  ],
  "confirmation_message": "「バフ」は #400バフ仕上げと鏡面バフ仕上げのどちらですか？"
}
```

ユーザー：

```text
#400で。
```

LLM：

```json
{
  "status": "ready",
  "operations": [
    {
      "action": "add",
      "process_code": "BUFF_400",
      "quantity": 1,
      "unit": "job"
    }
  ],
  "confirmation_message": null
}
```

---

# 12. LLMへ渡す情報

LLMには必ず以下を渡す。

## 現在の見積条件

```json
{
  "material": "SUS304",
  "quantity": 10,
  "additional_processes": []
}
```

## 利用可能な工程マスタ

```json
[
  {
    "process_code": "COUNTERSINK",
    "display_name": "皿穴加工",
    "aliases": ["皿もみ", "皿穴", "countersink"]
  },
  {
    "process_code": "BUFF_400",
    "display_name": "#400バフ仕上げ",
    "aliases": ["400番バフ", "#400", "バフ400"]
  },
  {
    "process_code": "BUFF_MIRROR",
    "display_name": "鏡面バフ仕上げ",
    "aliases": ["鏡面", "ミラーバフ", "鏡面磨き"]
  }
]
```

LLMはこの候補内から基本的にマッピングする。

候補に存在しない工程は勝手に価格を作らない。

`UNKNOWN_PROCESS` として返し、ユーザーに確認する。

---

# 13. LLM出力スキーマ

```python
class QuoteOperation(BaseModel):
    action: Literal["add", "update", "remove"]
    process_code: str
    quantity: float | None = None
    unit: str | None = None


class ChatInterpretation(BaseModel):
    status: Literal["ready", "needs_confirmation", "unknown"]
    operations: list[QuoteOperation]
    confirmation_message: str | None = None
```

Structured Output / JSON Schemaを使用する。

パースできない自由文は見積ロジックへ渡さない。

---

# 14. 表現揺れ対応

工程マスタにalias列を追加してもよい。

例：

```csv
process_code,display_name,aliases
COUNTERSINK,皿穴加工,"皿もみ|皿穴|countersink"
BUFF_400,#400バフ仕上げ,"#400|400番バフ|バフ400"
BUFF_MIRROR,鏡面バフ仕上げ,"鏡面|鏡面磨き|ミラーバフ"
```

処理順：

1. 完全一致
2. alias一致
3. LLMによる意味マッピング
4. 複数候補ならユーザー確認
5. 候補なしならUNKNOWN

---

# 15. チャットで変更可能にする項目

最低限以下を実装する。

## 工程追加

```text
「皿もみを2箇所追加して」
```

## 工程削除

```text
「バフはやっぱりなし」
```

## 工程変更

```text
「鏡面じゃなくて#400で」
```

## 数量変更

```text
「10個じゃなくて50個の場合は？」
```

## 材料変更

```text
「SUS304じゃなくてAL5052で計算して」
```

材料・数量変更時は見積全体を再計算する。

---

# 16. UI要件

Streamlitで1画面構成とする。

## 左カラム

### STEPアップロード

- `st.file_uploader`

### 基本条件

- 材料Selectbox
- 数量NumberInput

### ボタン

- 「初期見積を作成」

---

## 中央または上部

### CAD解析結果

表示例：

```text
CAD解析結果

外形寸法：100 × 80 × 20 mm
体積：120,000 mm³
表面積：22,000 mm²
面数：18
エッジ数：42
推定穴数：4
```

---

## 見積明細

表形式で表示する。

| 項目 | 数量 | 単価 | 金額 | 情報源 |
|---|---:|---:|---:|---|
| 材料費 | 1 | - | ¥2,100 | CAD+Master |
| 基本切削 | 38分 | ¥120 | ¥4,560 | CAD+Master |
| 穴加工 | 4 | ¥250 | ¥1,000 | CAD+Master |
| 段取り | 1 | ¥3,000 | ¥3,000 | Master |
| #400バフ | 1 | ¥3,500 | ¥3,500 | Chat |

下部に大きく表示：

```text
推定原価   ¥14,160
利益率     25%
----------------
最終見積   ¥17,700
```

---

## チャットエリア

`st.chat_message` / `st.chat_input` を使用する。

初期見積後にAIから以下を表示する。

```text
CAD情報と標準単価から初期見積を作成しました。
CADだけでは判断できない追加工程や条件変更があれば入力してください。
```

ユーザーとの会話後、変更内容を適用し、見積明細を即時再表示する。

---

# 17. Session State

StreamlitのSession Stateで以下を保持する。

```python
st.session_state.cad_features
st.session_state.quote_condition
st.session_state.quote_result
st.session_state.chat_history
st.session_state.pending_confirmation
```

ページ再描画で状態を失わないこと。

---

# 18. Chat Serviceの処理フロー

```text
ユーザー発言
   ↓
現在条件＋工程マスタ＋会話履歴をLLMへ送る
   ↓
JSON Structured Output
   ↓
status判定
   ├─ needs_confirmation
   │      ↓
   │   ユーザー確認
   │
   └─ ready
          ↓
      QuoteCondition更新
          ↓
      QuoteEngine再実行
          ↓
      新しいQuoteResult
          ↓
      UI更新
```

---

# 19. 安全策・設計原則

## LLMに金額計算をさせない

LLMが「この加工は3000円」と回答しても、その金額は使用しない。

必ず `process_rates.csv` を参照する。

## 未知工程は勝手に追加しない

```text
ユーザー：「特殊な焼け取りを追加」
```

マスタにない場合：

```text
「該当する登録工程が見つかりません。見積担当者による単価登録が必要です。」
```

とする。

## CAD解析結果も絶対視しない

穴数など簡易推定値には「推定」を付ける。

---

# 20. モックで重視するデモシナリオ

## Scenario 1：基本自動見積

1. STEPアップロード
2. 材料 `SUS304`
3. 数量 `10`
4. 初期見積作成
5. CAD特徴量と見積明細が表示される

## Scenario 2：追加加工

ユーザー：

```text
穴のうち2つは皿もみです
```

システム：

- `COUNTERSINK × 2` を追加
- 1,200円追加
- 見積再計算

## Scenario 3：曖昧表現

ユーザー：

```text
バフもお願いします
```

AI：

```text
#400バフ仕上げと鏡面バフ仕上げのどちらですか？
```

ユーザー：

```text
400番で
```

システム：

- `BUFF_400` に正規化
- 3,500円追加
- 見積再計算

## Scenario 4：条件変更

ユーザー：

```text
数量50個ならいくら？
```

システム：

- quantityを50へ変更
- 全体再計算
- 新見積を表示

## Scenario 5：材料変更

ユーザー：

```text
SUSじゃなくてアルミなら？
```

LLM：

```text
AL5052の意味で合っていますか？
```

確認後：

- materialをAL5052へ変更
- 材料費・加工費等を再計算

---

# 21. 実装順序

Codexは以下の順序で実装すること。

## Step 1

プロジェクト雛形を作成。

## Step 2

CSVマスタとMasterLoaderを実装。

## Step 3

Pydanticモデルを定義。

## Step 4

CadQueryによるSTEP解析を実装。

## Step 5

QuoteEngineを実装。

この時点でCLIまたはテストコードから、STEP → 初期見積が出ることを確認する。

## Step 6

Streamlit UIを作成。

## Step 7

LLM Clientを抽象化して実装。

`.env` のAPI Keyから接続する。

API Keyがない場合でも、MockLLMClientでデモ可能にする。

## Step 8

ChatQuoteServiceを実装。

## Step 9

チャット操作による見積追加・削除・変更を実装。

## Step 10

テストを作成。

---

# 22. Mock LLMモード

API Keyなしでもデモできるようにする。

以下のキーワードはルールで解釈する。

```text
皿もみ → COUNTERSINK
400番 / #400 → BUFF_400
鏡面 → BUFF_MIRROR
研磨 → POLISH
```

実LLMを使う場合は同じ `ChatInterpretation` モデルを返す。

```python
class BaseLLMClient(Protocol):
    def interpret_quote_change(...) -> ChatInterpretation:
        ...
```

これによりMockと実LLMを差し替え可能にする。

---

# 23. テスト要件

最低限以下をpytestでテストする。

## CAD解析

- STEPを読み込める
- Volume > 0
- Bounding Box > 0
- Face数 > 0

## 見積

- 材料変更で材料費が変わる
- 数量変更で価格が変わる
- COUNTERSINK追加で見積が増える
- BUFF_400追加で3,500円増える
- 工程削除で価格が戻る

## Chat

- 「皿もみ2つ」が `COUNTERSINK quantity=2` になる
- 「バフ」が確認質問になる
- 「400番」で `BUFF_400` に確定する
- マスタにない工程はUNKNOWNになる

---

# 24. 受入条件

以下がすべて動作すればモック完成とする。

1. STEPファイルをアップロードできる
2. Geometry情報を画面に表示できる
3. CSVマスタから初期見積を計算できる
4. 初期見積の明細を表示できる
5. ユーザーが自然言語で追加工程を指示できる
6. LLMが工程を構造化データに変換できる
7. 曖昧表現では確認質問できる
8. 確定した工程をCSV単価で再計算できる
9. 数量・材料をチャットで変更できる
10. 最終見積を表示できる
11. LLM自身が価格を生成しない
12. API KeyなしでもMockモードで動作する

---

# 25. READMEに記載する起動手順

想定：

```bash
python -m venv .venv
```

Windows：

```bash
.venv\Scripts\activate
```

```bash
pip install -r requirements.txt
streamlit run app.py
```

---

# 26. Codexへの実装方針

- まず動く最小構成を完成させること
- 過剰な抽象化を避けること
- Pythonコードは型ヒントを付けること
- 金額ロジックとLLMロジックを分離すること
- Streamlit固有コードを `app.py` に寄せること
- ビジネスロジックをUIから独立させること
- CSVの値はすべて疑似データであることをREADMEに明記すること
- CAD Feature Recognitionはモック品質でよく、精度を保証しないこと
- 将来、実企業データ・実原価モデルへ差し替えやすい構造にすること

---

# 27. 将来拡張（今回は実装不要）

以下はTODOとしてREADMEに記載する。

## 類似案件検索

CAD特徴量をベクトル化し、過去案件から類似部品を検索する。

```text
新規CAD
↓
Geometry Features
↓
Similarity Search
↓
過去見積・実績原価
↓
今回見積との比較
```

## 見積→実績フィードバック

受注後に以下を保存する。

- 見積工数
- 実績工数
- 見積原価
- 実績原価
- 最終販売価格

## 企業固有用語辞書

例：

```text
「いつもの磨き」 = BUFF_400
「仕上げA」 = POLISH
```

## Confidence Score

CAD解析・類似案件・LLM解釈の信頼度から、人間確認が必要か判定する。

## Excel連携

CSVマスタを、実際の既存Excel見積マクロやDBへ差し替える。

---

# 28. このモックで伝えたい価値

このモックの目的は、単なるCADビューアやLLMチャットを作ることではない。

以下の価値仮説をデモする。

```text
従来

CADを見る
↓
見積担当者が条件を判断
↓
Excelへ手入力
↓
特殊工程を思い出して追加
↓
見積完成


モック

CADアップロード
↓
基本情報・初期見積を自動生成
↓
AIと会話しながら特殊条件だけ補足
↓
自動再計算
↓
最終見積
```

特に重要なのは、CADだけでは取得できない現場固有の判断を排除するのではなく、LLMとのHuman-in-the-loopで補完することである。

この構成により、完全自動見積より技術リスクを抑えながら、見積担当者の入力工数・判断負荷・表現揺れを削減できる可能性を示す。
