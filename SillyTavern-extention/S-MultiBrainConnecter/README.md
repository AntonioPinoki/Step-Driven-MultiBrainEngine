# S-MultiBrainConnecter for SillyTavern

Step-driven-MultiBrainEngineとSillyTavernを連携するためのUI拡張機能です。

現在の版は **0.5.0** です。本体1.5.0の継続ロア・変数の状態継承には、この版以降が必要です。

SillyTavernから以下の情報を読み取り、確認用JSONとして表示・送信します。

- チャットIDとメッセージ数
- チャットメタデータ
- 現在のキャラクターまたはグループ
- グループの全メンバーと、ミュートされていないメンバーの名前・識別情報
- グループメンバーのミュート状態と、今回発言するキャラクター
- ユーザー名
- 直近メッセージの本文、役割、スワイプ情報、reasoning
- 生成種別（通常、再生成、スワイプ、Continueなど）

グループ情報の `all_characters` にはミュート状態を含む全メンバーを、
`active_characters` にはミュートされていないメンバーを格納します。
今回発言するキャラクターは `is_current_speaker: true` となり、将来の
`{{groupchar}}` と `{{allchar}}` のどちらにも含められるよう、両方の一覧へ必ず保持します。

生成開始時に、取得したJSONをローカルのStep-driven-MultiBrainEngineへ自動送信します。

```text
http://127.0.0.1:8001/api/sillytavern/context
```

送信先はこのPC内に限定され、送信失敗時も通常の生成は継続します。

## インストール

このフォルダをSillyTavernのユーザー拡張機能フォルダへ配置します。

```text
SillyTavern/data/default-user/extensions/S-MultiBrainConnecter/
```

配置後にSillyTavernを再読み込みし、拡張機能パネルの `S-MultiBrainConnecter` を開きます。

## 0.4.0からの更新

1. 現在の拡張フォルダーをバックアップします。
2. 本体と一緒に配布されたこのフォルダーの `index.js`、`manifest.json`、`style.css` を、上記のインストール先へ上書きします。
3. SillyTavernを再読み込みします。ブラウザーが旧JSをキャッシュしている場合はキャッシュを無効にして再読み込みします。
4. 手動送信後、本体の `GET /api/sillytavern/context` で `connector.version` が `0.5.0`、`connector.compatible` がtrueとなることを確認します。

以前の配布物には、同じ0.4.0表示でJSの内容が異なるものがありました。0.5.0では送信JSONにversion・build・capabilitiesを含め、本体が不足機能を診断します。これらは配備確認用であり、snapshotの照合や署名検証の代わりには使いません。旧版も通常送信は受理しますが、本体診断に更新案内が残ります。

生成時はSTの加工後の履歴を使ってsnapshotを作り、再生成・swipeで除かれた対象と照合がずれないようにします。グループで別話者のreasoningが省略される場合には、最新assistantにある署名付き状態を補足します。未署名の最新assistantを越えて古い状態を探すことはありません。

更新だけで失われた状態を自動復旧することはできません。必要なら元のswipe・分岐・バックアップを確認し、明示的に復元または状態リセットを行ってください。

## 使い方

1. SillyTavernでキャラクターとチャットを開きます。
2. 拡張機能パネルから `S-MultiBrainConnecter` を開きます。
3. `現在の情報を取得` を押します。
4. 表示されたJSONを確認します。

`JSONをコピー` で確認結果をクリップボードへコピーできます。
`Step-driven-MultiBrainEngineへ手動送信` で、Step-driven-MultiBrainEngineの起動状態と受信処理を確認できます。

自動送信された情報は、チャットIDがリクエストに含まれる場合はそのIDで、
それ以外は直近メッセージの役割と本文で生成リクエストへ照合されます。
一致候補が複数ある場合や一致を確認できない場合は、安全のためその情報を使用せず、
リクエスト本体から取得できる役割情報へフォールバックします。
