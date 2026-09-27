# S-MultiBrainConnecter for SillyTavern

Step-driven-MultiBrainEngineとSillyTavernを連携するためのUI拡張機能です。

現在の版は、SillyTavernから以下の情報を読み取り、確認用JSONとして表示・送信します。

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
