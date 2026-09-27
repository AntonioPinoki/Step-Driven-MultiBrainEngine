# Step-driven MultiBrainEngine

## Special Thanks

このプロジェクトは、[DonBananas](https://github.com/DonBananas)氏の[MultiAgent-BrainEngine-SillyTavern](https://github.com/DonBananas/MultiAgent-BrainEngine-SillyTavern)を基に、機能の追加・変更・再構成を行ったものです。素晴らしい元プロジェクトを公開してくださったDonBananas氏に、心より感謝します。

Step-driven MultiBrainEngineは、複数の推論ステップを順番に実行し、その結果を使って最終応答を生成するOpenAI互換プロキシです。

OpenAI互換のチャットクライアントから利用でき、特定のフロントエンドだけに限定されません。プロンプトの構成、各ステップの生成設定、プロバイダー、ロアブックなどはブラウザのコントロール画面から管理できます。

SillyTavern向けの連携拡張も同梱しています。この拡張を組み合わせると、キャラクターやグループチャットの情報をBrainEngineへ渡し、ロールプレイ向けの追加機能を利用できます。

## 主な機能

- 最大23個の推論ステップと、最終出力用Writer
- ステップごとに編集できるプロンプトと生成パラメーター
- Main providerと、任意のLogic providerの使い分け
- プロンプト構成のCSVプリセット保存、読込、Import、Export
- エージェント単位で割り当てられるLorebook
- 一時的な追加指示と調整を支援するPromptAssistant
- OpenAI互換のストリーミング／非ストリーミング応答
- 日本語／英語に対応したコントロール画面
- SillyTavern連携拡張によるキャラクター、ペルソナ、グループ情報の受け渡し

## 必要環境

- Windows
- Python 3.10以上
- 接続先となるOpenAI互換API
- BrainEngineへ接続できるOpenAI互換クライアント

SillyTavernは任意です。SillyTavern固有の連携機能を使用するときだけ必要になります。

## セットアップ

プロジェクト直下で仮想環境を作成し、依存パッケージをインストールします。

```powershell
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

すでに`.venv`がある場合、仮想環境の作成は不要です。

## 起動

このプロジェクトの起動入口は`engine\start_web_ui.py`です。プロジェクト直下で次のコマンドを実行します。

```powershell
.venv\Scripts\python.exe engine\start_web_ui.py
```

サーバーの起動後、コントロール画面がブラウザで開きます。

```text
http://127.0.0.1:8001/ui/
```

初回は`Providers`画面でMain providerを設定し、保存してください。

- Base URL: 利用するOpenAI互換APIのURL
- Model: 接続先で使用するモデル名
- API key: 接続先のAPIキー

Logic providerは任意です。有効にすると主に推論ステップで使用され、最終出力にはMain providerが使用されます。無効の場合は、すべてMain providerで実行されます。

設定内容はローカルの`engine/config.json`へ保存されます。このファイルはAPIキーを含むため、共有や公開をしないでください。

## クライアントから接続する

OpenAI互換クライアントのBase URLを次のように設定します。

```text
http://127.0.0.1:8001/v1
```

チャット補完エンドポイントは次のURLです。

```text
http://127.0.0.1:8001/v1/chat/completions
```

クライアントから送られた会話メッセージをもとに、設定済みの推論ステップを実行してからWriterが最終応答を生成します。

## コントロール画面

### Dashboard

BrainEngineと各プロバイダーの接続状態を確認します。

### Providers

Main providerと、任意のLogic providerを設定します。接続テストもこの画面から実行できます。

### Prompt Studio

推論ステップ、Writer、Summarization、グループチャット用プロンプトを編集します。ステップの追加・削除、実行順、タイトル、生成パラメーターも設定できます。

設定はプリセットとして`Preset`フォルダへ保存でき、CSVによる共有やImport、Exportにも対応しています。

### Lorebooks

SillyTavern互換のWorld Info JSONを読み込み、推論ステップまたはWriterへ個別に割り当てます。Lorebookはキーワードや常時発動の設定に応じて、必要なエージェントのプロンプトへ追加されます。

### PromptAssistant

現在のプロンプト構成と最近の出力を参照しながら、調整方針を相談できます。保存済みプロンプトを変更せず、エージェントごとの一時指示を試すこともできます。一時指示はBrainEngineを再起動すると消去されます。

## SillyTavernとの連携

BrainEngine本体はSillyTavern専用ではありません。同梱の`SillyTavern-extention/S-MultiBrainConnecter`は、SillyTavernのコンテキストをBrainEngineへ補足するための任意拡張です。

拡張を組み合わせると、次の情報を利用できます。

- ユーザーペルソナと現在のキャラクター
- キャラクターカード
- グループメンバーとミュート状態
- 生成種別や直近メッセージの情報
- `{{user}}`、`{{char}}`、`{{groupchar}}`、`{{allchar}}`の展開

SillyTavern側では、接続先をCustom（OpenAI-compatible）として設定し、Base URLに`http://127.0.0.1:8001/v1`を指定します。

拡張を使用しないクライアントでも、通常のOpenAI互換メッセージを使ってBrainEngineへ接続できます。

## サマライズ

Prompt Studioには、通常の推論チェーンとは独立したSummarization設定があります。メッセージ列の末尾へ次のマーカーだけを含むメッセージを送ると、推論ステップとWriterを経由せず、Main providerで要約を生成します。

```text
[[SUMMARIZE]]
```
<img width="477" height="202" alt="image" src="https://github.com/user-attachments/assets/b704585d-34a3-4e1c-8379-5a9d1512e52e" />

返された要約にも同じマーカーが付与されます。履歴に含めることで、その後の応答で過去の文脈として参照できます。

## データと記憶


登録済み推論ステップは、現在キャラクターの直近3件の応答に含まれる `[DEEP DIVE]` から、同じステップ名の出力全文を継承します。現在ステップの指示に続く1つのuserメッセージへ、現在ターン内の前ステップ出力、過去出力0〜3件（古い順）、現在ステップの最終制御指示を明確に区切ってまとめます。過去出力は参考記録として扱い、完成済みassistant応答の継続生成にはしません。表示用の短縮スナップショットは継承に使用せず、Writerにはこの履歴を追加しません。

1対1チャットでは名前のないassistantメッセージにも対応します。グループチャットでは、連携拡張のキャラクター情報とメッセージの名前または明示的な `キャラクター名:` のラベルを使い、話者を識別できない履歴は除外します。

プリセットIDを切り替えると、サーバー全体で最初の通常推論チェーン1回だけ継承を除外し、ロアブックのキーワード走査からも過去の思考を除きます。Summarization、Impersonate、Setting、Continueおよび登録ステップがない生成では、この除外を消費しません。次の通常生成からは同名タグの履歴が再び対象となるため、直近3件に切替前の出力が残っていれば継承されます。

ステップ名は空白・句読点の除去や小文字化などでタグ名に正規化されます（例: `行動・動作` → `行動動作`）。正規化後のタグが他の登録ステップと重複する設定は保存できません。名称変更でタグが変わると、変更前の出力は継承されません。

SillyTavern連携拡張から受け取るコンテキスト、プリセット切替判定、PromptAssistantの一時指示はメモリ上だけに保持され、BrainEngineの再起動時に消去されます。

主なローカルデータは次の場所に保存されます。

```text
engine/config.json       プロバイダー設定とAPIキー
engine/prompts.json      現在有効なプロンプト設定
Preset/                  CSVプリセット
Lorebooks/books/         Lorebook JSON
Lorebooks/settings.json  Lorebookの設定と割り当て
```

## プロジェクト構成

```text
engine/                  BrainEngine本体とコントロール画面
Preset/                  プロンプトプリセット
Lorebooks/               Lorebook本体と設定
SillyTavern-extention/    任意のSillyTavern連携拡張
requirements.txt         Python依存パッケージ
```

## License

本プロジェクトは、利用・改変・無料再配布・商用サービスでの利用・生成物の商用利用を許可する一方、ソフトウェア自体の販売を禁止する独自のソースアベイラブル・ライセンスで提供します。これはOSI認定のオープンソースライセンスではありません。

派生元の`MultiAgent-BrainEngine-SillyTavern`に由来する部分には、元のMITライセンスが引き続き適用されます。

- [Step-driven MultiBrainEngine Source-Available License 1.0](LICENSE)
- [Upstream MIT License](LICENSE-UPSTREAM-MIT)
- [Attribution and license notice](NOTICE)
