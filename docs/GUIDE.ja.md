# オペレーターガイド — touchdesigner-bridge-mcp

AIチャット（Claude Desktopまたは任意のMCPエージェント）からこのMCPを操作するための、日常的な運用リファレンスです。
操作方法、それを安全に保つメンタルモデル、各ツールファミリー、実際のファサードコンテンツを構築する
作業ループ、そのループを成功させる規約、そして運用上の落とし穴までを扱っており、セッションを即座に
生産的に始められます。すべてのステップが型付き・検証済みのデータ専用オペレーションであるため、AIを傍らに
置いてTouchDesignerを*学ぶ*ための低リスクな手段としても役立ちます — やりたいことを記述すると、AIが
ライブセッション内でオペレーターネットワークを構築し、それが形になっていく様子を観察できます。初回の
インストールとセットアップについては[README](../README.md)と[docs/INSTALL.md](INSTALL.md)を、各オペレーターの
正確なパラメーターについては`operator_reference`ツールにライブで問い合わせるか、その事実と公式ドキュメントの
リンクを得るには`help`を参照してください。

---

## メンタルモデル

このブリッジは**構造的にデータ専用**です。アシスタントは、型付き・スキーマ検証済みのツールから成る固定の
レジストリ — オペレーターごとに1つの作成・設定ツールと、少数のユーティリティ動詞 — のみを呼び出せます。
任意コード実行ツールも、生スクリプトの経路も、自由形式のコードシンクも意図的に存在しません。サーバーが
実行できることの集合は、列挙されたツールリスト*そのもの*です。この2つの帰結がすべてのセッションを形作ります：

- **AIがネットワークを構築し、あなたが重い処理を発火する。** アシスタントは、マッピングされた映像を生成する
  オペレーターグラフ（TOP、CHOP、SOP、COMP、MAT、DAT、POP）を組み立てて調整します。レンダリング、ファイルの
  ベイク、ライブネットワーク送信はすべて**ワイヤー接続のみ** — ブリッジは`record`/`active`を**オフ**にした
  ままオペレーターを構築して配線し、それを*あなた*（またはあなたのメディアサーバー）が発火します。アシスタントが
  自らレンダリング、ベイク、送信をトリガーすることはありません。
- **コンテンツのオーサリングは物理的なワープの手前で終わる。** ここでのTouchDesignerの役割は*コンテンツ制作*
  です。実際のサーフェスへの最終的なワープ/ブレンドは、下流のメディアサーバー（Pixera / disguise）が行います。
  ブリッジはピクセルをオーサリングし、その受け渡しを配線できますが、現地でのキャリブレーションは人間のステップです。

---

## 操作方法

チャットからMCPツールを呼び出すことで、**ライブのTouchDesignerネットワーク**を構築します。役立つ習慣：

- 最初に`td_capabilities`を呼び出して全体像を把握します — これは全ツール面（ファミリー別のツール数）、
  データ専用の境界、そして質問を適切な検索ツールにルーティングする方法を返します。その後`scene_info`で
  現在のシーンとビルドを確認します。
- ツールは1つの**作業ディレクトリ**（ゲートウェイGUIで設定）内でのみ読み書きできます。データファイルは
  **そのルートからの相対**パスで参照してください — サブフォルダは辿れます（例：
  `assets/sections/section_00.obj`）。ルートの外にあるものはすべて、シンボリックリンク経由でも拒否されます。
- 操作の前に検査を：`read_network`（ネットワークの構造 + 配線、非デフォルトのパラメーターは`pars=true`）、
  `operator_reference`（任意のオペレーターの正確な型付きスキーマ。`search=<部分文字列>`はオペレーター検索）、
  `find_errors`（サブツリーが壊れている理由）、`top_info`（安価な数値TOP状態 — 解像度、GPUメモリ、クック統計）。
- 結果を*見る*には：`save_top`はTOPの実際のピクセルを返し、`capture_ui`は任意のオペレーター自身のノード
  ビューア（CHOPグラフ、SOP/geometryCOMPの3Dビュー、MATプレビュー）をTouchDesigner自身のGPUバッファから
  レンダリングします。

---

## インストールとarm（一度きりの要約）

詳細な手順は[README](../README.md) / [docs/INSTALL.md](INSTALL.md)にあります。要約すると：

1. ゲートウェイをビルドします：`cargo build --release --manifest-path gateway/Cargo.toml`。これは1つの
   ファイル`gateway/target/release/touchdesigner-bridge-mcp.exe`を生成し、これがGUIとヘッドレスMCP
   サーバーの両方を兼ねます。
2. 引数なしでゲートウェイを実行してGUIを開き、**Working dir**を設定して**Apply**をクリックします（これは
   `working_dir`を`~/.touchdesigner-bridge-mcp/arm.json`にマージ書き込みします）。ライブの監査ログを見るために
   実行したままにしておきます。
3. ゲートウェイをMCPクライアントに登録し、ビルド済みの`.exe`を指すようにして、環境変数に
   `TDMCP_GW_HEADLESS=1`と`TDMCP_REPO=<クローンパス>`を設定します。クライアントを完全に再起動します。

**エグゼキューターをarmする：** TouchDesignerを開き、Textport（`Alt+T`）を開いて、1行のarmコマンドを
貼り付けます（GUIは**Copy arm command**ボタンとともにそれを表示し、`python scripts/setup.py`もそれを
出力します）。armはエグゼキューターのファイルを`INTEGRITY.json`と照合し、いずれかのハンドラーがコード実行の
エンドポイントに見える場合はarmを拒否し、セッショントークンを発行し、`/mcp_bridge`コンポーネント
（`127.0.0.1:9980`上のループバックWeb Server DAT）とGUIの同意トグルを組み立てます。ディスク上の編集を
ホットリロードするには、**いつでも再arm**してください。

**ファイアウォールのステップは不要です** — Web Server DATは`127.0.0.1`のみにバインドするため、
マシン外でリッスンするものはありません。

**作業ディレクトリを変更する：** GUIの**Working dir**フィールドに新しいルートを入力して**Apply**を
クリックします。これは`arm.json`を書き換え、エグゼキューターとゲートウェイの両方にライブで反映されます —
再起動も再armも不要です。

---

## 作業ループ

実際の成果物はすべて、同じ「把握 → 発見 → 構築 → 検証 → 納品」の流れに従います：

1. **把握** — 全体像と境界には`td_capabilities`、既に存在するものには`scene_info` / `read_network`。
2. **ルートを発見** — `recipe_reference classify=<持っているもの / やりたいこと>`は、タスクを入口となる
   レシピにマッピングするルーティングテーブルを返します。その後`recipe=<id>`で、順序付き・ツールに
   マッピングされたステップを取得します。66個のレシピは、規約（後述）を運ぶ実例であり、ブラックボックス
   ではありません。
3. **構築** — 各オペレーター自身の作成・設定ツールを名前で呼び出します（`geometryCOMP`、`renderTOP`、
   `noiseCHOP`、…）。これはノードを作成し、そのパラメーターを1回の呼び出しで設定します。パラメーターは
   まず`operator_reference optype=<名前>`で調べ、多数のオペレーターを1往復で実行するには`batch`を使います。
   ノードは`connect`で配線し、既存のノードは`set_par` / `set_par_many`で調整し、フラグは`set_flags`で
   設定します。
4. **各ステップを検証** — レシピはステップごとに安価なチェックを運びます：`read_network`（構造）、
   `find_errors`（クックエラー）、`top_info`（解像度/状態）、`capture_ui` / `save_top`（見る）。各ランドマークに
   `OUT_`ヌルを植えて、後のステップが安定した名前を狙えるようにします。
5. **納品** — 出力オペレーター（`moviefileoutTOP`、`ndioutTOP`、`windowCOMP`、…）を`record`/`active`を
   オフにして**ワイヤー接続のみ**で配線します。それをあなたが発火します。

---

## ツールファミリー

カタログは**合計544ツール** — 7つのTouchDesignerファミリーにまたがる**509個のオペレーターツール**に加えて、
**35個のユーティリティ / イントロスペクションツール**です。すべてのオペレーターツールは、1回の呼び出しで
1つのオペレータータイプを作成・設定し、4つの予約された配置引数（`op_name`、`parent_path`、`pos_x`、`pos_y`）を
受け付けます。

- **TOP — イメージ / テクスチャ（106）** — GPUラスターファミリーであり、ファサードコンテンツの中核：
  ジェネレーター（`noiseTOP`、`rampTOP`、`constantTOP`、`textTOP`、`circleTOP`、`rectangleTOP`）、
  コンポジット（`overTOP`、`addTOP`、`multiplyTOP`、`compositeTOP`、`crossTOP`、`switchTOP`）、グレード/FX
  （`levelTOP`、`hsvadjustTOP`、`blurTOP`、`lumablurTOP`、`edgeTOP`、`embossTOP`、`displaceTOP`、
  `remapTOP`）、`feedbackTOP`のトレイルループ、メディアI/O（`moviefileinTOP`、`moviefileoutTOP`、
  `ndiinTOP`/`ndioutTOP`、`syphonspoutinTOP`/`syphonspoutoutTOP`、`videodeviceinTOP`）、3Dから2Dへの心臓部
  である`renderTOP`、そしてアライメントオペレーター（`cornerpinTOP`、`lensdistortTOP`、
  `scalabledisplayTOP`、`viosoTOP`）。
- **CHOP — チャンネル / 信号 / タイミング（137）** — アニメーションと制御データのファミリー：ジェネレーター
  （`lfoCHOP`、`noiseCHOP`、`patternCHOP`、`waveCHOP`、`constantCHOP`）、数学/シェイプ（`mathCHOP`、
  `filterCHOP`、`lagCHOP`、`limitCHOP`、`functionCHOP`）、シーケンシング（`timerCHOP`、`beatCHOP`、
  `timecodeCHOP`、`countCHOP`、`triggerCHOP`）、ライブ入力（`oscinCHOP`、`midiinCHOP`、`dmxinCHOP`、
  `audiodeviceinCHOP`、`audiospectrumCHOP`）、そして出力（`dmxoutCHOP`、`oscoutCHOP`、`midioutCHOP`）。
  `nullCHOP`は推奨される安定したエクスポートのタップです。
- **SOP — ジオメトリ / サーフェス（79）** — `fileinSOP`（`.obj`/`.tog`をロード）、`boxSOP`、`sphereSOP`、
  `gridSOP`、`circleSOP`、`textSOP`、変換/編集（`transformSOP`、`copySOP`、`mergeSOP`、`noiseSOP`、
  `extrudeSOP`）、加えてエンドポイントのタップとしての`nullSOP`。
- **COMP — コンポーネント / 3D / コンテナ（30）** — 3DシーンとUIのファミリー：`geometryCOMP`（Render TOPが
  描画するオブジェクト）、`cameraCOMP`、`lightCOMP`、`environmentlightCOMP`、`nullCOMP`（変換のタップ）、
  `baseCOMP`/`containerCOMP`（グループ化/UI）、`windowCOMP`（出力ウィンドウ）、`usdCOMP`/`fbxCOMP`
  （シーンのインポート）、そしてBullet物理COMP群。
- **MAT — マテリアル / シェーディング（10）** — `constantMAT`（フラットでライト非依存 — 一般的な
  プロジェクションの選択肢）、`phongMAT` / `pbrMAT`（ライティングあり）、`pointspriteMAT`（ポイントごとの
  ビルボード）、`lineMAT`、`depthMAT`、そしてタップMAT群。
- **DAT — データ / テーブル / 参照（51）** — `tableDAT`、`fileinDAT`、`folderDAT`、`choptoDAT`、
  `oscinDAT`/`oscoutDAT`、`midiinDAT`、`artnetDAT`、`opfindDAT`、`infoDAT`、`errorDAT`、そしてタップDAT群。
- **POP — ポイントオペレーター（96）** — ポイントクラウド / ポイントインスタンシングのファミリー
  （例：`pointfileinPOP`、`toptoPOP`、`soptoPOP`、`dmxfixturePOP`）。TOPインスタンシングの経路より新しいため、
  ライブビルドでパラメーターを検証してください。
- **ユーティリティ / イントロスペクション（35）** — 共有される動詞と検索：構築（`connect`、`set_par`、
  `set_par_many`、`set_flags`、`set_pos`、`delete_op`、`pulse`、`bind_chop`、`write_csv`、`import_scan`、
  `import_segmented_model`）、見る（`show`、`save_top`、`capture_ui`、`top_info`、`read_network`、
  `find_errors`、`inspect`、`mem`、`scene_info`）、参照（`td_capabilities`、`help`、`operator_reference`、
  `recipe_reference`、`glsl_reference`、`expr_reference`、`code_reference`）、`batch`メタツール、2つの
  同意ゲート付きコードレーンツール（`set_glsl`/`validate_glsl`、`set_expr`/`validate_expr`）、そして
  同意ゲート付きの`device_send`（クローズドなPJLinkプロジェクター制御）。

NVIDIA/CUDA専用のオペレーターは意図的にスコープ外です（AMD優先のターゲットではテスト不能なため）。

---

## よくあるワークフロー

以下のすべてのパスは作業ディレクトリ内に制限されており、各レシピIDは`recipe_reference recipe=<id>`で
完全な形で取得できます。

### 建物モデル → フレーミングされ、シェーディングされたレンダー（`facade_3d_render`）

```
geometryCOMP(op_name="geo")                       # render flag on
fileinSOP(parent_path="/geo", file="assets/building.obj")
delete_op(op="/geo/torus1")                       # drop the default torus child
set_flags(op="<fileinSOP>", render=true, display=true)
constantMAT(op_name="mat", color=[1,1,1])         # a MAT is mandatory or geo renders black
set_par(op="/geo", pars={material:"/mat"})
cameraCOMP(op_name="cam", projection="ortho")
renderTOP(op_name="render", camera="/cam", geometry="geo*", outputresolution="custom", resolutionw=3840, resolutionh=2160)
constantTOP(op_name="bg", color=[0,0,0])          # render output is transparent
overTOP(op_name="beauty")                          # connect render over bg
nullTOP(op_name="OUT_render")
```

Render TOPは、カメラ/ジオメトリ/ライトを入力に配線するのではなく、**パラメーターによって**参照します。
その出力は透明です — 暗い`constantTOP`の上にコンポジットしてください。`constantMAT`はライトを必要としません。
`phongMAT`/`pbrMAT`はライトがないと黒くレンダリングされます。

### ジェネレーティブな2Dファサードコンテンツ（`generative_texture_fx`）

`noiseTOP` → `rampTOP` → `levelTOP` → `transformTOP` → `compositeTOP` → `feedbackTOP` → `blurTOP` →
`nullTOP (OUT_facade_tex)`。ノイズを変化させるには`noiseTOP.tz`をアニメーション（または`lfoCHOP`から
駆動）します。`feedbackTOP.top`を下流のコンポジットに向けてトレイルループを閉じ、収束するように
フェード（`levelTOP.opacity < 1`）を与えます。これは`OUT_render`のポストプロセスレーンでもあります。

### セグメント化されたファサードへの映像、ワンコール（`segmented_facade_video_projection`）

セクションのOBJを作業ディレクトリ配下（`assets/sections/`）にコピーすると、`import_segmented_model`が
`sec*`/`mat*`のリグ全体を1回の呼び出しで構築し（File In SOPをCONSTANTモードに強制して、TDのデフォルトの
サンプルボックスではなく実際のOBJがロードされるようにします）、`set_par_many`が`emitmapcoord=uv0`とともに
`moviefileinTOP`をすべての`matNN.emitmap`に駆動します。セクションごとに1つのマテリアルが必須です — TDは
マージされたメッシュの一部にマテリアルをスコープできません。

### プロジェクター / ファイル / メディアサーバーへのアライメントと受け渡し（`projection_align_and_output`）

データ専用の4コーナーキーストーン（`cornerpinTOP`のpin*コーナー、`mapping=perspective`）を適用し、
アライメントされたヌルを植えてから、出力を配線します — `windowCOMP`のパフォームウィンドウ、
`moviefileoutTOP`（HAPはメディアサーバーのデフォルトコーデック）、`ndioutTOP`、または`syphonspoutoutTOP`。
ワイヤー接続のみ：ウィンドウは開かれず、`record`/`active`はオフのままです。現地での6ポイント
キャリブレーションは人間のステップであり、その数値は`set_par`経由で入力し直されます。

残りについて — セクションごとのカラー/ライト（`per_section_color_choreography`）、高さスイープ
（`height_sweep_choreography`）、キューショー（`facade_cue_choreography`、`show_control_timecode`）、
ウィンドウマスキング（`facade_mask_atlas`）、マルチプロジェクターブレンド（`multiprojector_edge_blend`）、
ライブ入力（`realtime_input_driver`）、DMX出力（`dmx_artnet_output`）、メッシュワープ
（`mesh_freeform_warp`）、ポイントクラウド（`point_cloud_content`）、キャリブレーションされた3Dプロジェクション
（`projector_calibrated_3d`） — は`recipe_reference classify=<タスク>`から始めてください。

---

## それを機能させる規約

- **`OUT_`ヌルを植える。** 各レシピステップはランドマークとなるヌル（`OUT_render`、`OUT_facade_tex`、
  `OUT_geo`、…）で終わります。下流のFXと出力レーンは、移動するかもしれないノードではなく、その**名前**を
  狙います。`nullTOP`/`nullCHOP`/`nullSOP`は、すべてのチェーンの末尾で推奨される安定したタップです。
- **作成ツールのタプレット vs `set_par`のコンポーネント名。** オペレーター自身の作成ツールは**ベクトル**
  パラメーター — `color:[r,g,b]`、`resolution:[w,h]`、`t`/`r`/`s` — を取り、ゲートウェイがそれらを
  コンポーネントに展開します。`set_par`は代わりに**生の**コンポーネント名（`colorr`、`tx`、`resolutionw`）を
  取ります。生のコンポーネント名や`pars{}`ラッパーを*作成ツールに*渡すと、それらは黙って破棄されます
  （作成ツールのパラメーターではないため）。同様に、タプレットベクトルを`set_par`に渡しても反映されません。
  規約をツールに合わせてください。
- **CHOP駆動のアニメーションには`bind_chop`。** パラメーターをCHOPチャンネル（LFO、オーディオレベル、
  タイマー）からアニメーションするには`bind_chop`を使います — これはコードではなくデータの*バインディング*
  であり、完全にデータ専用の境界内にあります。バインディングでは表現できないロジックの場合にのみ、
  式レーンに手を伸ばしてください。
- **256pxの解像度トラップは実在する。** TOPジェネレーター（`noiseTOP`、`rampTOP`、`constantTOP`）は256pxが
  デフォルトで、チェーン全体がそれを継承します。4K納品のためには、**ソース**に`outputresolution=custom` +
  `resolutionw`/`resolutionh`を設定してください。（非商用ビルドは出力を1280に制限します。）
- **ガバナーは助言的である。** `set_par`（および`set_par`に落ちるすべての作成ツール）は、要求された解像度 /
  インスタンス数 / レンダーパスがリアルタイムGPUに対して大きい場合、`{level: ok|caution|heavy, note}`
  フラグを付加します — 成果物の予算に合わせてスケールダウンし、ツールの上限まで構築しないでください。
  本当に破滅的でドライバーを停止させる規模のものだけがハード拒否されます。ライブの帯域については`mem`を
  呼び出してください。

---

## コードレーンと学習ツール

このブリッジはデータ専用ですが、TouchDesignerのアートはしばしばコード — GPUシェーダーや自己計算する
パラメーター — を必要とします。それらは2つの狭い、**デフォルトオフ・同意ゲート付き**のレーンを通じて
入ります。そしてアシスタントの役割は、その機会を*浮上させて教える*ことであり、決して自ら生コードに
手を伸ばすことではありません：

- **GLSL**（`glsl_reference` → `set_glsl` / `validate_glsl`、フラグ`allow_glsl`） — 固定TOPでは表現できない
  カスタムのピクセルごとのフィールド、FX、ワープ/ディスプレイスメント、フィードバック。GPUサンドボックス
  内で実行され、最悪の場合でも回復可能なドライバーリセットで済みます。
- **パラメーター式**（`expr_reference` → `set_expr` / `validate_expr`、フラグ`allow_expr`） — パラメーターを
  ライブで計算する単一行のPython式。これは最もゲートの厳しいレーン（ホストのPythonとして評価される）で
  実験的な段階で出荷されています。CHOPで駆動できるものには`bind_chop`を優先してください。
- **DAT / コールバックPython** — フルCPython、除外された生の経路。AIはコードを提案して教えますが、
  **決してツールを通じて配信しません**。あなたが自分でそれをDATに貼り付けます。

`code_reference`が入口です — どのレーンが何を運ぶか、共有される同意ハンドシェイク、そしてレシピステップが
フラグする`glsl_opportunity` / `expr_opportunity`のキュー。**AIは自らのレーンを有効化できません。** 同意
フラグは立ち入り禁止の設定ディレクトリにあり、GUIにいる人間（またはarmブートストラップ）だけがそれを
切り替えられます。レーンがオフのとき、AIはテキストを提案して説明し、あなたがそれを貼り付けます — どちらの
道でも境界は保たれます。

---

## それでTouchDesignerを学ぶ

型付きの面は学習の足場です：AIは*実際のTouchDesignerオペレーター*にしか到達できず、それらは
アプリケーションと同じ構成で整理されているため、AIを制約する面がそのまま構造をあなたに教えます。
すべての呼び出しはゲートウェイGUIのライブ監査ログに流れ込むので、実行中のセッションでネットワークが
ノードごとに構築されるのを観察できます。ミスは目に見え、安価です — 任意コードを実行するものも、
プロジェクトフォルダ外のファイルに触れるものもないため、間違ったノードは災害ではなく、あなたが気づいて
修正できるものです。何かを頼み、それがどのようにグラフを配線するかを観察し、`recipe_reference`を、実際の
ファサードコンテンツがどのように構築されるかを教える組み込みのチューターとして使ってください。

---

## 運用上の落とし穴

- **レンダーはワイヤー接続のみ。** `moviefileoutTOP`、`ndioutTOP`、およびライブ送信は`record`/`active`を
  **オフ**にして構築されます — アシスタントが自らそれらを発火することはありません。`save_top`、`write_csv`、
  `capture_ui`はファイルを*書き出します*（作業ディレクトリ内へ）。特に`moviefileoutTOP.record`と
  `dmxoutCHOP.active`が、あなたのゲートされたアクションです。
- **MATは必須**であり、そうでなければジオメトリは黒くレンダリングされます。`phongMAT`/`pbrMAT`はライトも
  必要とします。File In SOPのrenderフラグを設定する前に、`geometryCOMP`のデフォルトのトーラス子を削除して
  ください。さもないと両方がレンダリング対象を巡って競合します。
- **Render TOPはカメラ/ジオメトリ/ライトをパラメーターで受け取り**、入力ワイヤーでは受け取りません — その
  唯一の入力配線は、暗いプレート上への下流のコンポジットです。
- **数値はクランプされ、拒否されない** — 範囲外の値は許容される最小/最大に固定されるため、奇妙な結果を伴う
  「成功」はクランプされていた可能性があります。
- **`import_segmented_model`はOBJを作業ディレクトリ配下に必要とする** — 外部パスはパス制限によって空白化
  されます。`emitmapcoord=uv0`の映像マッピングが登録されるためには、セクションがUVを持っている必要が
  あります。
- **ファイルパスは作業ディレクトリ内になければならない** — その外にあるもの（または設定ディレクトリ、
  エグゼキューターパッケージ）はすべて拒否されます。トラストルートのファイルについては、作業ディレクトリ内で
  あっても拒否されます。
