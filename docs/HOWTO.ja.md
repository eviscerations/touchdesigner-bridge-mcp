# ハウツー

生きたドキュメント — ツールがテストされるにつれて成長していきます。以下のステップはいずれも型付き・検証済みでデータ専用のオペレーションであるため、これらのフローはAIに駆動させながらTouchDesignerを*学ぶ*安全な手段としても機能します。各タスクは、実際のツール群と、順序立った完全なステップを担うレシピへの短いインデックスです — 任意のレシピの全文は`recipe_reference recipe=<id>`で、任意のオペレーターの正確なパラメーターは`operator_reference optype=<name>`で取り出せます。

## インストール（一度きり）

1. ゲートウェイをビルドします：`cargo build --release --manifest-path gateway/Cargo.toml`（または`gateway/`から`cargo build --release`）。生成されるファイルは1つ：`gateway/target/release/touchdesigner-bridge-mcp.exe`で、GUIとヘッドレスMCPサーバーの両方を兼ねます。
2. 引数なしで実行してGUIを開き、**Working dir**（作業ディレクトリ）を設定して**Apply**をクリックします。
3. ゲートウェイをMCPクライアントに登録し、`.exe`に向けたうえで環境に`TDMCP_GW_HEADLESS=1`と`TDMCP_REPO=<clone path>`を設定し、その後クライアントを完全に再起動します。

> **最短経路：** `python scripts/setup.py`はゲートウェイをビルドし、このマシン向けの実パスを埋め込んだClaude Desktop設定**と**Textportのarmコマンドの両方を出力します。

## エグゼキューターをアームする

TouchDesignerを起動し、**Textport**（`Alt+T`）を開いて、armの行を貼り付けます（clone pathはご自身のものに置き換えてください）：

```python
import os; os.environ['TDMCP_REPO']=r'C:/path/to/touchdesigner-bridge-mcp'; exec(open(os.path.join(os.environ['TDMCP_REPO'],'arm.py')).read())
```

GUIには、この行そのものが**Copy arm command**ボタンとともに表示されます。エグゼキューターに到達できるようになると、**Status**ペインに「Armed」ピルと稼働中のTouchDesignerビルドが表示されます。ディスク上の編集をホットリロードするには、**いつでも再アーム**してください。ブリッジは`127.0.0.1`のみにバインドするため、ファイアウォールの設定手順は不要です。

## 作業ディレクトリを変更する

GUIの**Working dir**フィールドに新しいルートを入力し、**Apply**をクリックします。`arm.json`に書き込まれ、エグゼキューターとゲートウェイの両方にライブで反映されます — 再起動も再アームも不要です。

## セットアップを検証する

ブラウザで`http://127.0.0.1:9980/health`を開き、新しいチャットでアシスタントに`scene_info`の実行を依頼してください。応答が正常に返れば、クライアント → ゲートウェイ → ループバック → エグゼキューター → TouchDesigner という経路全体が確認できたことになります。

---

## こんなときは…

各項目には、実際のツール群と、順序立ったステップ・各ステップの`OUT_`ランドマーク・検証チェック・落とし穴を確認するために`recipe_reference recipe=<id>`で開くレシピIDが記載されています。

### …ビルモデルを暗いプレート上にレンダリングするには？

`geometryCOMP` + `fileinSOP`（`.obj`/`.tog`を読み込みます。他の形式には`fbxCOMP`/`alembicSOP`/`usdCOMP`を使用）→ デフォルトのトーラスを削除し、`set_flags(render,display)` → `constantMAT`（必須。設定しないと黒くレンダリングされます）→ `cameraCOMP` → `renderTOP`（カメラ/ジオメトリを**パラメーター経由で**参照）→ `constantTOP`の暗いプレート → `overTOP` → `nullTOP OUT_render`。レシピ：**`facade_3d_render`**。

### …ジェネレーティブなコンテンツ（パターン、エネルギー、光のスイープ、トレイル）を作るには？

`noiseTOP` → `rampTOP` → `levelTOP` → `transformTOP` → `compositeTOP` → `feedbackTOP`（その`top`を下流のコンポジットに向けてトレイルのループを閉じます）→ `blurTOP` → `nullTOP OUT_facade_tex`。256pxのデフォルトを回避するには、ソースに`outputresolution=custom` + `resolutionw/h`を設定します。レシピ：**`generative_texture_fx`**。

### …ビルに映像を投影するのを1回の呼び出しで行うには？

セクションのOBJを`assets/sections/`配下にコピーすると、`import_segmented_model`が`sec*`/`mat*`のリグ全体を一度に構築し、`set_par_many`が`moviefileinTOP`をすべての`matNN.emitmap`へ`emitmapcoord=uv0`で駆動します。セクションはUVを持っている必要があります。レシピ：**`segmented_facade_video_projection`**（既存の手組みリグには**`segmented_facade_video_content`**）。

### …各ファサード部位を独立してアドレスするためのセクション別リグを構築するには？

`import_segmented_model`（または、セクションごとに`geometryCOMP`を1つ・MATを1つ手組みします — TDはマージされたメッシュの一部にマテリアルをスコープできません）。これは、コレオグラフィ・カラー・映像・マスキングの各レーンがすべてアニメートする土台となります。レシピ：**`per_section_material_rig`**。

### …コードを書かずにパラメーターをアニメートするには？

**`bind_chop`**を使ってパラメーターをCHOPチャンネル（`lfoCHOP`、`audiospectrumCHOP`、`timerCHOP`、`patternCHOP`）にバインドします — これはコードではなくデータバインディングであり、データ専用の境界の内側に完全に収まります。1つのLFOで駆動する移動する光のリビール（reveal）には、レシピ：**`height_sweep_choreography`**。

### …セクション別のカラー・明るさ・フェードを（映像なしで）駆動するには？

各セクションのエミットはエクスポート可能な3つのスカラーであるため、明るさ × セクション別RGBで任意のセクション別カラーが得られます。`write_csv`のキューテーブルからカラーをキーフレーム化するか、定数カラーにアニメートした明るさを掛け合わせます。レシピ：**`per_section_color_choreography`**。

### …タイムライン・GOボタン・タイムコードを備えたキュー付きショーを実行するには？

`tableDAT`のキューテーブル（作業ディレクトリのCSVから）が`timerCHOP`（Ease In/Out、On Done = Re-Start）を駆動し、各セクションをキューを通してグライドさせます。GOは許可リスト済みの`pulse`アクチュエーターで、`timecodeCHOP`がLTC/MTCにロックします。レシピ：**`facade_cue_choreography`**と**`show_control_timecode`**。

### …ライブのOSC / オーディオ / DMX / MIDI入力に反応するには？

共有のrename → merge → null → CHOPエクスポートの適用ブロックに、ソースノード — `oscinCHOP`、`audiodeviceinCHOP`、`dmxinCHOP`、`midiinCHOP` — だけを差し替えます。残りはスイープと同一です。レシピ：**`realtime_input_driver`**。

### …投影コンテンツを実際の窓や凹部に当てないようにするには？

グレースケールのUV空間マスク（窓 = 0 → 黒のテクセル）をTOP空間でエミットコンテンツに掛け合わせ、その積を`emitmap (uv0)`に割り当てます。窓に位置合わせされた不規則なマスクは、上流でベイクされたアセットです。レシピ：**`facade_mask_atlas`**。

### …コンポジットをアライン/キーストーン補正してプロジェクターに送るには？

`cornerpinTOP`（pin*の各コーナー、`mapping=perspective`）でデータ専用の4コーナーキーストーンを適用し、アライン済みのnullを配置してから、`windowCOMP`のパフォームウィンドウ・`moviefileoutTOP`・`ndioutTOP`・`syphonspoutoutTOP`のいずれかに配線します — **配線のみ**で、ウィンドウは開かず、`record`/`active`はオフのままにします。現場のキャリブレーション値は`set_par`経由で入力し直します。レシピ：**`projection_align_and_output`**。

### …コンポジットを重なり合う2台のプロジェクターに分割し、継ぎ目をブレンドするには？

プロジェクターごとに：`cropTOP`のスライス + `cornerpinTOP`のキーストーン + `rampTOP`のブレンドマスク + `levelTOP`のガンマ + `multiplyTOP` → プロジェクター別の`windowCOMP`とし、マスクの合計が1になるようにします。オーバーラップ幅とガンマは現場で計測して入力し直します。レシピ：**`multiprojector_edge_blend`**。

### …曲面や不規則な面にコンテンツをワープさせるには？

恒等UVフィールド（`rampTOP` + `reorderTOP`）を構築し、コントロールグリッドのオフセット（`write_csv` → `tableDAT` → `dattoCHOP` → `choptoTOP` → `blurTOP`）を加え、`remapTOP`で適用します — これはkantanMapperのデータ専用版に相当します。頂点ごとのインタラクティブなドラッグは人間が行うステップです。レシピ：**`mesh_freeform_warp`**。

### …完成したコンテンツをメディアサーバーや4Kファイルに引き渡すには？

`OUT_render` / `OUT_facade_tex`を`moviefileoutTOP`（HAP / HAP Qがメディアサーバーのデフォルトコーデック）またはNDI/Spoutを供給する`nullTOP`にタップします — **配線のみ**で、`record`/`active`はオフのままにします。録画はあなたが押します。レシピ：**`output_handoff`**。

### …TouchDesignerからDMX / Art-Net / sACNのフィクスチャを駆動するには？

ルートA：CHOPのチャンネルセット → `dmxoutCHOP`（その`active`はデフォルトで**オン**なので、ブリッジがオフに保ちます）。ルートB：色付きのPOP → `dmxfixturePOP` → `dmxoutPOP`によるピクセルマッピング。ライブ運用への移行は、あなたの配線のみのステップです。レシピ：**`dmx_artnet_output`**。

### …ドローンのポイントクラウドをコンテンツとしてレンダリングするには？

`pointfileinTOP`（完成した`.ply`/`.exr`/`.xyz`クラウドを取り込みます。フォトグラメトリ自体は外部で行います）→ 正規化 → クラウドTOPを供給元とする`geometryCOMP`（インスタンシングをオン）で`pointspriteMAT`を用いてポイントにジオメトリをインスタンシング → フレーミングしてレンダリング。レシピ：**`point_cloud_content`**。

### …仮想カメラを実際のファサードに一致させるには？

平坦な1:1マップには`cameraCOMP`で`projection=ortho` + `orthowidth`を、実際のレンズに一致させるには`projection=perspective` + `viewanglemethod=focalaperture`を使用し、ファサード中心の`nullCOMP`ターゲットに向けます。レシピ：**`camera_match_facade`**。キャリブレーション済みのプロジェクター錐台から3Dジオメトリにコンテンツを投射するには、レシピ：**`projector_calibrated_3d`**。

### …GLSLシェーダーを書くには？

シェーダーのテキストは`glsl_reference`（topic=`patterns`/`environment`/`functions`、または search=<substring>）に尋ねます。`allow_glsl`を有効にしている場合は`set_glsl`で適用します（TouchDesignerがコンパイルする前に検証されます）。有効にしていない場合は、そのテキストを`glslTOP`の`pixeldat`に配線したText DATに貼り付けます。AIが自ら実行することは決してありません。

### …式（expression）でパラメーターを計算するには？

CHOPで駆動できるものは何であれ`bind_chop`を優先してください。バインディングでは表現できないロジックについては、`expr_reference`（topic=`common`/`allowed`）に単一行の式を尋ねます。`allow_expr`を有効にしている場合は`set_expr`で適用します（許可リスト優先で検証されます）。有効にしていない場合は、パラメーターに手で入力します。これは最もゲートの厳しいレーンです。コード面全体と同意ハンドシェイクがどのように組み合わさるかは`code_reference`を参照してください。

---

## 検査とナビゲート

- `td_capabilities` — まずここから始めるためのオリエンテーション：面全体、データ専用の境界、そして質問の振り分け方。
- `scene_info`、`read_network`（非デフォルトのパラメーターには`pars=true`）— シーンに何があるか / ネットワークの構造と配線。
- `operator_reference`（`optype=<name>`、またはオペレーターファインダーとしての`search=<substring>`）、`help` — ノードの正確な型付きスキーマ、あるいはそのファクトと公式のDerivativeドキュメントへのリンク。
- `find_errors` — サブツリーをスキャンしてエラーを報告しているオペレーターを見つけ、自己修正できるようにします。
- `top_info` — TOPの数値状態を安価に取得（解像度、GPUメモリ、クック統計）。`mem` — 稼働中のリソース帯域。
- `save_top` — TOPの実際のピクセル。`capture_ui` — 任意のオペレーター自身のノードビューア（CHOPグラフ、3Dビュー、MATプレビュー）を、TouchDesigner自身のGPUバッファから取得します。
