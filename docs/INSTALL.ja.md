# TouchDesigner Bridge MCP のインストールと実行

このガイドは、クリーン後のクローンから、AIクライアント（Claude Desktop）がブリッジ経由で
TouchDesignerのネットワークを構築するまでの手順を説明します。以下の各ステップは、
実際のコード（`gateway/src/main.rs`、`gui.rs`、`config.rs`、`arm.py`）に照らして検証済みです。

ブリッジはループバック経由で通信する**2つのプロセス**で構成されます：

- **ゲートウェイ** — 1つのRustバイナリ `touchdesigner-bridge-mcp[.exe]`。これはMCPサーバー
  *と*GUIの**両方**を兼ねます。どちらになるかは起動時に決まります（後述）。AIクライアントは
  これをMCPサーバーとして起動し、あなた自身は作業ディレクトリを設定するためにGUIとして実行します。
- **エグゼキューター** — 実行中のTouchDesignerセッションの**内部**で動作するPythonです。
  TD Textportに1行を貼り付けることでアーム（arm）します。TouchDesigner組み込みのPythonと標準
  ライブラリのみを使用します — `pip install`するものは何もありません。

両者は単一のファイル `~/.touchdesigner-bridge-mcp/arm.json` を介してランデブーします。

> **ファストパス：** `python scripts/setup.py` はゲートウェイをビルドし、記入済みのClaude Desktop
> 設定**と**このマシンの実際のパスが入ったTextportアームコマンドを表示します。以下の手動手順は、
> それが生成する内容を説明するものです。

---

## 1. 前提条件

| 要件 | 備考 |
|---|---|
| **Rustツールチェーン** | ゲートウェイのビルドに必要。<https://rustup.rs> からインストールしてください。`cargo`を提供します。 |
| **TouchDesigner** | ライセンス済みのインストール、ビルド **2023.11k+** または **2025.30k+**。エグゼキューターはTD自身のPython内で動作します。 |
| **Python 3.10+** | `scripts/setup.py` ヘルパーを実行する場合にのみ必要です。*エグゼキューター*はこのPythonではなく、TouchDesignerの組み込みPythonで動作します。 |
| **Claude Desktop** | または、stdio MCPサーバーを起動できるその他のMCPクライアント。 |

対象とするパイプラインは**AMDファースト**であり、NVIDIA/CUDA専用のオペレーターは意図的に
対象外としています。主要プラットフォームはWindowsです。

---

## 2. クローンとゲートウェイのビルド

```sh
git clone <your-fork-or-source-url> touchdesigner-bridge-mcp
cd touchdesigner-bridge-mcp
cargo build --release --manifest-path gateway/Cargo.toml
```

これにより、次の場所にバイナリが生成されます：

```
gateway/target/release/touchdesigner-bridge-mcp.exe      (Windows)
gateway/target/release/touchdesigner-bridge-mcp          (macOS/Linux)
```

**1つのバイナリがどのようにモードを選択するか**（`gateway/src/main.rs`）：起動時に環境変数
`TDMCP_GW_HEADLESS` をチェックします。

- **設定あり** → stdin/stdout経由でMCPプロトコルを提供します（ヘッドレス）。MCPクライアントは
  この方法で起動しなければなりません。
- **未設定**（デフォルト） → GUIウィンドウを開きます。

つまり*同じ実行ファイル*がサーバーとGUIの両方であり、環境変数がその切り替えスイッチです。

---

## 3. MCPクライアントの設定（Claude Desktop）

ゲートウェイをClaude Desktopの設定に追加し、ClaudeがそれをMCPサーバーとして起動できるように
します。設定ファイルは次の場所にあります：

- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

[`claude_desktop_config.example.json`](claude_desktop_config.example.json) のテンプレートを
使用してください。`touchdesigner` ブロックを実際の設定にコピーし、2つの
`<ABSOLUTE_PATH_TO_CLONE>` プレースホルダーを両方ともクローンのパスに置き換えます
（Windowsでもスラッシュ `/` を使用）：

```json
{
  "mcpServers": {
    "touchdesigner": {
      "command": "C:/path/to/touchdesigner-bridge-mcp/gateway/target/release/touchdesigner-bridge-mcp.exe",
      "env": {
        "TDMCP_GW_HEADLESS": "1",
        "TDMCP_REPO": "C:/path/to/touchdesigner-bridge-mcp"
      }
    }
  }
}
```

2つの環境変数はいずれも重要な役割を担います：

- **`TDMCP_GW_HEADLESS=1`** — 必須です。これがないとクライアントはGUIウィンドウを起動して
  しまい、MCPハンドシェイクが完了しません。
- **`TDMCP_REPO`** — クローンのパス。これにより、ゲートウェイは同梱の参照データ
  （`reference/recipes.json`、`reference/catalog.json`）を確定的に見つけ、arm.pyの解決も
  一致します。（設定しない場合、ゲートウェイはバイナリの親ディレクトリを遡って探す方式に
  フォールバックします。通常は `gateway/target/...` レイアウトから動作しますが、設定して
  おくのが確実な選択です。）

設定を編集したら、Claude Desktopを再起動してください。

---

## 4. 作業ディレクトリの設定（GUIの実行）

**作業ディレクトリ**は、ツールが読み書きできる唯一のフォルダです — すべてのエグゼキューター
のファイル操作はこの配下に限定されます。これは意図的にソースツリーとは**別**にしています。

引数を**付けずに**ゲートウェイバイナリを実行すると、GUIが開きます：

```sh
gateway/target/release/touchdesigner-bridge-mcp.exe
```

GUIで **Working dir** ペインを開き、既存のフォルダを入力（または貼り付け）して、**Apply** を
クリックします。これは2つのことを行います（`gateway/src/gui.rs`）：

1. 以降のすべての呼び出しに対して、確認（confinement）ルートをライブで更新します。
2. `working_dir` を `~/.touchdesigner-bridge-mcp/arm.json` にマージ書き込みします。

### `arm.json` — 唯一の信頼できる情報源

`~/.touchdesigner-bridge-mcp/arm.json` は、ゲートウェイとエグゼキューターがランデブーする
場所です。双方がこれを読み込み、GUIと `arm.py` がこれを書き込みます。保持する内容は次のとおりです：

| キー | 意味 |
|---|---|
| `working_dir` | 確認ルート。両レイヤーが**呼び出しごとに新鮮に**読み込みます。 |
| `token` | エグゼキューターがアーム時に生成するCSPRNGセッショントークン。ゲートウェイはすべてのループバック呼び出しでこれを提示します。あなたが入力したり目にしたりすることはありません。 |
| `port` | エグゼキューターがリッスンするループバックポート（デフォルト `9980`）。 |
| `allow_expr`, `allow_glsl` | コード/式レーンの同意トグル（デフォルトはオフ）。 |
| `allow_highres` | レンダーマグニチュード上限のバイパス（デフォルトはオフ）。 |
| `enabled`, `min_action_interval_ms` | 自動アームフラグと、破壊的呼び出しのスロットル。 |

**再アーム（re-arm）は同意フラグと作業ディレクトリを保持します** — 単なる再アームが、
確認ジェイル（jail）を密かにリセットしたり、レーンを無効化したりすることは決してありません。

---

## 5. TouchDesigner内でのエグゼキューターのアーム

TouchDesignerを開き、**Textport**（`Alt+T`）を開いて、アームコマンドを貼り付けます。
それは次のようになります（あなたのクローンのパスに置き換えてください）：

```python
import os; os.environ['TDMCP_REPO']=r'C:/path/to/touchdesigner-bridge-mcp'; exec(open(os.path.join(os.environ['TDMCP_REPO'],'arm.py')).read())
```

手で入力する必要はありません。GUIの **Settings → Arm bridge (Textport)** ペインには、
この正確な行が **Copy arm command** ボタンとともに表示され、`python scripts/setup.py`
もこれを表示します。

アームが行うこと（`arm.py`）：

- ディスク上のエグゼキューターファイルを `td_executor/INTEGRITY.json` に照合して検証します。
  インポートする**前**に行います（フェイルクローズの改ざん検知）。
- いずれかのハンドラーが生のコード実行エンドポイントに見える場合はアームを拒否します
  （データ専用のカナリア `assert_no_rce_endpoints`）。
- セッショントークンを生成し、`token`/`port`/`working_dir` を（そして同意フラグを保持しつつ）
  `arm.json` に書き込みます。
- `/mcp_bridge` コンポーネントを組み立てます：ループバック `127.0.0.1:9980` 上の
  **Web Server DAT** に加えて、ディスク上の `td_executor` パッケージを読み込む薄い
  コールバックDAT、そして `arm.json` に永続化されるGUIの**同意トグル**（Allow Expr Lane /
  Allow GLSL Lane）を追加します。

**いつでも再アーム**して、ディスク上のエグゼキューターの編集をホットリロードできます —
行を再実行すると、まずモジュールキャッシュがパージされます。ブリッジを**削除**するには：

```python
op('/mcp_bridge').destroy()
```

> `arm.py` は `exec()` 経由で実行され、そこでは `__file__` が未定義であるため、`TDMCP_REPO`
> が注入されます — この環境変数は、arm.pyが `td_executor` パッケージと整合性の信頼ルートを
> 見つける方法です。これは確認用の作業ディレクトリ**ではありません**。

---

## 6. 検証

1. **エグゼキューターのヘルスチェック** — TouchDesignerをアームした状態で、ブラウザまたはcurlで開きます：

   ```
   http://127.0.0.1:9980/health
   ```

   エグゼキューターに到達可能になると、GUIの **Status** ペインにも「Armed」のピルと
   ライブのTouchDesignerビルドが表示されます。

2. **クライアントにツールが表示される** — Claude Desktopを再起動します。`touchdesigner`
   MCPサーバーが接続し、ツールサーフェス（オペレーターツールに加えて `scene_info`、
   `read_network`、`connect`、`set_par` などのユーティリティ）を公開するはずです。
   アシスタントに `scene_info` を呼び出すよう依頼してください — 正常に応答が返れば、
   全経路（クライアント → ゲートウェイ → ループバック → エグゼキューター → TouchDesigner）が
   稼働していることを意味します。

---

## トラブルシューティング

| 症状 | 考えられる原因・対処 |
|---|---|
| クライアントにサーバーは表示されるがツールがない／ハングする | 設定の `env` に `TDMCP_GW_HEADLESS` が設定されていない。バイナリがstdioを提供する代わりにGUIを起動した。 |
| ツールが接続エラー／403で失敗する | TouchDesignerがアームされていないか、`arm.json` が古い。アームコマンドを再実行し、`http://127.0.0.1:9980/health` を確認してください。 |
| 「integrity pre-check FAILED, refusing to arm」 | マニフェストを再生成せずにエグゼキューターファイルが変更された。`python scripts/gen_integrity_manifest.py` を実行するか、ローカル開発時のみ `TDMCP_INTEGRITY=0` を設定してください。 |
| ファイルツールがパスを拒否する | パスが作業ディレクトリの外にある。GUIのWorking-dirペインで正しいフォルダを設定してください（Apply）。 |
| 参照／レシピの検索が失敗する | `TDMCP_REPO` がクローン（`reference/recipes.json` を含むもの）を指していない。クライアント設定の `env` で修正してください。 |
| Expr/GLSLレーンが拒否される | これらのレーンはデフォルトでオフです。TouchDesignerの `/mcp_bridge` 上の同意トグルをオンにしてください（`arm.json` に永続化されます）。 |

セキュリティモデルとツールサーフェスについてはトップレベルの [README.md](../README.md) を、
プロジェクトごとにTD側のアームを永続化する方法については
[`td_package/README.md`](../td_package/README.md) を参照してください。
