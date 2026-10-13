# AWS への載せ方（インフラ担当者向けの引き継ぎ資料）

このシステム（板金部品の見積）を AWS に載せるための資料です。アプリ側の準備はできています。この資料のとおりに AWS の部品を作ってつなげば動きます。
アプリのコードは手元（ローカル）と AWS で同じです。違うのは、環境変数と、どの入口から起動するかだけです。

- 画面：React（`web/`、`npm run build` で `web/dist` を作る）
- API：FastAPI（`api/`）
- 処理：`src/`
- データベース：Aurora DSQL（PostgreSQL も可）
- ファイル：S3

## 1. 全体の形

```
ブラウザ ──> CloudFront（app.example.com）
               ├─ /api/*  ──> API Gateway（HTTP API）──> Lambda「api」（estimate-api イメージ）
               │                                            ├─ Aurora DSQL（データベース）
               │                                            ├─ S3（ファイル）
               │                                            └─ Step Functions に受付を渡す
               └─ それ以外 ──> S3（画面：web/dist）

ブラウザ ──> S3（図面・STEP の送り先。API が出す署名付きのフォームで直接送る。大きいファイルの取り出しも直接）

Step Functions「estimate-jobs」（受付1件 = 実行1回）
   Prepare  ──> Extract ──> Finish        どこかで失敗したら ──> Fail
   (api)       (extract)    (api)                              (api)
   DBを読む     形の解析      DBに書く                            受付を「失敗」にする
               図面の読み取り
               （DBなし）
```

- **API は小さいイメージ、抽出だけ大きいイメージ**にしてあります。CadQuery（形の解析の部品、約1.2GB）は抽出の Lambda にだけ入っています。API の Lambda は CadQuery なしで動きます（`tests/test_aws.py` で確認しています）。
- 抽出の段はデータベースに触りません。DB の権限は要りません。必要なのは S3 と Claude API のキーだけです。
- 受付（解析や見積の計算）は時間がかかります。そのため API はすぐに受付番号を返し、画面がその番号で進み具合を聞きに来ます。API Gateway の 30 秒の制限にはかかりません。

## 2. 作るもの

| 部品 | 名前（例） | 設定 |
|---|---|---|
| ECR | `estimate-api`、`estimate-extract` | 下の「3. イメージ」で作ったものを置く |
| S3（ファイル） | `estimate-files-<環境>` | 公開しない。CORS とライフサイクル（「6. S3」） |
| S3（画面） | `estimate-web-<環境>` | `web/dist` の中身を置く。CloudFront からだけ読む（OAC） |
| Lambda | `estimate-api` ほか4つ | 「4. Lambda」 |
| Step Functions | `estimate-jobs`（標準） | `deploy/aws/jobs_state_machine.asl.json` |
| API Gateway | HTTP API | `$default` ルートを Lambda `estimate-api` に（ペイロード 2.0） |
| CloudFront | `app.example.com` | 「7. CloudFront」 |
| Aurora DSQL | クラスター1つ | 「8. データベース」 |
| Cognito | ユーザープール1つ | 「9. ログイン」 |
| Secrets Manager | `estimate/anthropic` | Claude API のキー（図面PDFの読み取りに使う） |

環境は、本番とは別に**試験環境を1つ**作ってください。ローカルでは確かめられないこと（起動の遅さ、必要なメモリ、権限）は試験環境で確かめます。

## 3. イメージ

`Dockerfile.lambda` から2つのイメージを作ります。土台は `python:3.11-slim` です。手元の `Dockerfile` と同じ Python 3.11 で、Lambda で動かすための部品（`awslambdaric`）を足してあります。AWS の Python 3.11 用の土台イメージは使えません。中の OS（Amazon Linux 2）が古く、CadQuery の OpenCASCADE が動かないためです。

```
docker build -f Dockerfile.lambda --target api     -t estimate-api .       # 約440MB
docker build -f Dockerfile.lambda --target extract -t estimate-extract .   # 約3.3GB（Lambda の上限は 10GB）
docker tag / docker push で ECR へ
```

アーキテクチャは x86_64 です。CadQuery の部品に arm64 版がないものがあります。

## 4. Lambda（5つ。イメージは2つ）

同じイメージから、入口（イメージの CMD）を変えて作ります。入口は関数の設定「イメージの上書き → CMD」で指定します。

| 関数 | イメージ | CMD（入口） | メモリ | タイムアウト | 役目 |
|---|---|---|---|---|---|
| `estimate-api` | estimate-api | `api.aws_lambda.api` | 1024MB | 30秒 | API Gateway から呼ばれる API 本体 |
| `estimate-prepare` | estimate-api | `api.aws_lambda.prepare` | 1024MB | 60秒 | 受付の1段目（DB を読んで計画を作る） |
| `estimate-extract` | estimate-extract | `src.services.aws_extract.handler` | 3008MB〜（要確認） | 900秒 | 2段目（形の解析・図面の読み取り。DB なし） |
| `estimate-finish` | estimate-api | `api.aws_lambda.finish` | 1024MB | 120秒 | 3段目（照合・金額・類似。DB に書く） |
| `estimate-fail` | estimate-api | `api.aws_lambda.fail` | 512MB | 30秒 | 失敗の記録 |

ほかに、デプロイのたびに1回だけ呼ぶ入口があります。`api.aws_lambda.migrate` です（`estimate-prepare` の CMD を一時的に変えて呼ぶか、同じイメージで関数をもう1つ作ってください）。データベースの表を最新にし、初期データを入れます。どの関数も最初の呼び出しで同じことをします。ただ、空のデータベースに同時に何本も来るとぶつかるので、先に1回済ませておきます。

メモリは本物で測って決めてください。抽出は大きい STEP ほどメモリを使います。最初の1回は、イメージを読み込むので数秒〜十数秒かかります。

### 環境変数

| 変数 | 入れる関数 | 値 |
|---|---|---|
| `DATABASE_URL` | api・prepare・finish・fail | `auroradsql+psycopg://admin@<クラスターのエンドポイント>/postgres?region=ap-northeast-1` |
| `ESTIMATE_STORAGE_URL` | 5つとも | `s3://estimate-files-<環境>/app` |
| `JOBS_STATE_MACHINE_ARN` | api | Step Functions `estimate-jobs` の ARN |
| `COGNITO_USER_POOL_ID` | api | 例 `ap-northeast-1_AbCdEf123` |
| `COGNITO_CLIENT_ID` | api | 画面用のアプリクライアントの ID |
| `COGNITO_DOMAIN` | api | `https://<接頭辞>.auth.ap-northeast-1.amazoncognito.com`（または独自ドメイン） |
| `ANALYSIS_ANTHROPIC_API_KEY` | api・extract | Claude API のキー（Secrets Manager から）。api は「読み取りできるか」の表示にだけ使う |
| `MAX_UPLOAD_MB` | api | 受け付ける最大のファイル（既定 50） |
| `DIRECT_DOWNLOAD_MB` | api | これより大きいファイルは S3 から直接取り出す（既定 4。Lambda の応答は約6MBまで） |
| `CORS_ORIGINS` | api | 画面と API を別のドメインにするときだけ。例 `https://app.example.com` |

`ESTIMATE_STORAGE_DIR` はイメージの中で `/tmp/estimate` に決めてあります（Lambda が書けるのは `/tmp` だけです）。その他の変数は README.md の「設定」にあります。

### 権限（実行ロール）

| 関数 | 必要な権限 |
|---|---|
| api | S3 `GetObject`・`PutObject`（`app/*`）、`states:StartExecution`（estimate-jobs）、`dsql:DbConnectAdmin`（クラスター）、Secrets Manager の読み取り |
| prepare・finish・fail | S3 `GetObject`・`PutObject`（`app/*`）、`dsql:DbConnectAdmin` |
| extract | S3 `GetObject`・`PutObject`（`app/*`）、Secrets Manager の読み取り。**DB の権限は付けない** |
| Step Functions | 4つの関数の `lambda:InvokeFunction` |

S3 の署名付きのフォームとリンクは api の権限で作ります。api に `PutObject` が要るのはそのためです。

## 5. Step Functions

`deploy/aws/jobs_state_machine.asl.json` が定義です。`${PrepareFunctionArn}` など4か所を関数の ARN に置き換えてください（CDK・SAM・Terraform の置き換えの機能が使えます）。種類は標準（STANDARD）です。

- 入力は `{"job_id": "j_..."}` です。API が `StartExecution` で渡します。実行の名前は受付番号です。同じ受付が二重に動くことはありません。
- 段と段の間で渡るのは `{"job_id", "tenant_id", "kind"}` だけです。計画と結果は S3 の `app/jobs/<受付番号>/` に JSON で置きます。Step Functions の状態は 256KB までなので、こうしています。
- どこかの段で失敗すると `Fail` が受付を「失敗」にし、画面に理由が出ます。利用者向けのメッセージ（`JobError`）はそのまま出ます。それ以外は「処理中にエラーが発生しました」になります。

## 6. S3（ファイル）

- パブリックアクセスはすべてブロックします。ブラウザは API が出す署名付きのフォームとリンク（数分で切れる）だけで読み書きします。
- **CORS**：ブラウザから直接送る・取り出すために必要です。

```json
[{"AllowedOrigins": ["https://app.example.com"], "AllowedMethods": ["GET", "POST"], "AllowedHeaders": ["*"], "MaxAgeSeconds": 600}]
```

- **ライフサイクル**：`app/jobs/`（受付の途中の計画と結果）は、7日ほどで消してかまいません。`app/uploads/`・`app/documents/`・`app/viewer/`・`app/tenants/` は消さないでください（図面・帳票・3D表示のファイル）。
- 会社ごとのファイルは `app/tenants/<会社ID>/...` に分かれています。

## 7. CloudFront

画面と API を同じドメインで受けます。こうするとブラウザから見て同じ所になり、CORS の設定が要りません。

| ビヘイビア | オリジン | 設定 |
|---|---|---|
| `/api/*` | API Gateway | キャッシュしない（CachingDisabled）。オリジンリクエストポリシーは `AllViewerExceptHostHeader`（Authorization ヘッダーを渡す）。メソッドはすべて許可 |
| 既定（`*`） | S3（画面、OAC） | `index.html` 以外はキャッシュしてよい。403・404 は `/index.html`（200）を返す（画面の URL を直接開けるように） |

画面を作り直したら `web/dist` を S3 に置き直し、`/index.html` の無効化をしてください。

## 8. データベース（Aurora DSQL）

- クラスターを1つ作ります。接続は IAM の権限（`dsql:DbConnectAdmin`）です。パスワードは要りません。接続のたびに一時トークンを作ります。
- 表と初期データ（材料・工程・表面処理のマスター、過去見積）は、`api.aws_lambda.migrate` を1回呼ぶと入ります（「4. Lambda」）。
- DSQL は、同時の書き込みがぶつかると後の方を失敗させます。アプリはそれをやり直すように作ってあります（`src/db/retry.py`・`api/conflict.py`）。
- PostgreSQL（Aurora Serverless v2 など）にする場合は、`DATABASE_URL=postgresql+psycopg://user:pass@host/db` にするだけです。そのときは Lambda を VPC に入れ、DB への経路を作ってください。DSQL なら VPC は要りません。

## 9. ログイン（Cognito）

- ユーザープールにカスタム属性 `custom:tenant_id`（会社ID。英小文字・数字・`-`・`_`、40文字まで）を作り、利用者ごとに入れます。
- アプリクライアント：パブリック（シークレットなし）、認可コード＋PKCE、スコープ `openid email profile`。
  - コールバック URL：`https://app.example.com/auth/callback`
  - サインアウト URL：`https://app.example.com/`
- ドメイン（Cognito のドメインか独自ドメイン）を `COGNITO_DOMAIN` に入れます。
- 詳しくは README.md の「ログイン（Amazon Cognito）」にあります。

## 10. 決まっている制限

| 項目 | 値 | 対応 |
|---|---|---|
| API の1回の応答 | 約6MB（Lambda） | 4MB を超えるファイル（図面PDF・3D表示）は S3 からの直接の取り出しに切り替わる |
| API の1回の要求 | 約6MB（Lambda） | 図面・STEP・DXF は S3 へ直接送る。書類の取り込み（書類と検索）と過去見積の CSV は API を通るので、約4MBまで |
| API の待ち時間 | 30秒（API Gateway） | 時間のかかる処理は受付（Step Functions）で動く |
| 抽出の1回 | 15分（Lambda） | ふつうの部品は数秒〜数十秒 |
| ファイルの大きさ | 50MB（`MAX_UPLOAD_MB`） | S3 へ送るフォームにも同じ上限が入る |

## 11. 手元で確かめる

AWS に載せる前に、手元で同じイメージを Lambda と同じ形で動かせます。AWS のアカウントは要りません。

```
docker build -f Dockerfile.lambda --target api -t estimate-api .
docker build -f Dockerfile.lambda --target extract -t estimate-extract .
python scripts/verify/lambda_images.py --rie <aws-lambda-rie のパス>
```

- `aws-lambda-rie` は Lambda の実行環境の代わりです（[GitHub](https://github.com/aws/aws-lambda-runtime-interface-emulator) の releases にあります）。
- この確認では、5つの関数をそれぞれ別のコンテナで立てます。`jobs_state_machine.asl.json` のとおりに Prepare → Extract → Finish を呼び、API に図面1件の登録と見積をさせます。
- ファイルとデータベースは手元のフォルダー（SQLite）で代わりにします。
- テスト（`python -m pytest -q tests/test_aws.py tests/test_e2e_s3.py`）も通してあります。中身はこうです。
  - 状態機械の定義どおりに受付を動かすと、スレッドで動かしたときと同じ金額になる
  - CadQuery なしで API 側が動く
  - S3（moto）への直接の送り・取り出しを本物のブラウザで確かめる
