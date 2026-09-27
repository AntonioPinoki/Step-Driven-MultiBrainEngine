const MODULE_NAME = 'brainengine_connector';
const PANEL_ID = 'brainengine-connector-panel';
const BRAINENGINE_CONTEXT_URL = 'http://127.0.0.1:8001/api/sillytavern/context';

function normalizeGenerationType(rawType) {
    const value = String(rawType ?? 'unknown').trim().toLowerCase();
    const knownTypes = new Set([
        'normal', 'regenerate', 'swipe', 'continue',
        'impersonate', 'quiet', 'manual',
    ]);
    return knownTypes.has(value) ? value : 'unknown';
}

function getCurrentCharacter(context) {
    if (context.characterId === undefined || context.characterId === null) {
        return null;
    }

    return context.characters?.[context.characterId] ?? null;
}

function getCurrentGroup(context) {
    if (!context.groupId) {
        return null;
    }

    return context.groups?.find(group => String(group.id) === String(context.groupId)) ?? null;
}

function firstString(...values) {
    const value = values.find(candidate => typeof candidate === 'string');
    return value ?? '';
}

function getDepthPrompt(character) {
    const depthPrompt = character?.data?.extensions?.depth_prompt
        ?? character?.extensions?.depth_prompt
        ?? character?.data?.depth_prompt
        ?? character?.depth_prompt;

    if (typeof depthPrompt === 'string') {
        return depthPrompt;
    }

    if (depthPrompt && typeof depthPrompt === 'object') {
        return firstString(depthPrompt.prompt, depthPrompt.value, depthPrompt.text);
    }

    return '';
}

function makeCharacterCardSnapshot(character) {
    const data = character?.data ?? {};

    return {
        description: firstString(character?.description, data.description),
        personality: firstString(character?.personality, data.personality),
        scenario: firstString(character?.scenario, data.scenario),
        depth_prompt: getDepthPrompt(character),
        creator_notes: firstString(
            character?.creator_notes,
            data.creator_notes,
            character?.creatorcomment,
            data.creatorcomment,
        ),
    };
}

function makeCharacterSnapshot(character, index, disabledMembers, currentCharacter) {
    const avatar = character?.avatar ?? null;
    const isCurrentSpeaker = Boolean(
        currentCharacter
        && (character === currentCharacter || (avatar && avatar === currentCharacter.avatar))
    );

    return {
        index,
        name: character?.name ?? character?.data?.name ?? null,
        avatar,
        muted: Boolean(avatar && disabledMembers.has(avatar)),
        is_current_speaker: isCurrentSpeaker,
    };
}

function makeGroupSnapshot(context, group, currentCharacter) {
    if (!group) {
        return null;
    }

    const memberAvatars = Array.isArray(group.members) ? group.members : [];
    const disabledMemberAvatars = Array.isArray(group.disabled_members)
        ? group.disabled_members
        : [];
    const disabledMembers = new Set(disabledMemberAvatars);
    const allCharacters = memberAvatars
        .map(avatar => {
            const index = context.characters?.findIndex(character => character?.avatar === avatar) ?? -1;
            const character = index >= 0 ? context.characters[index] : null;
            return character
                ? makeCharacterSnapshot(character, index, disabledMembers, currentCharacter)
                : null;
        })
        .filter(Boolean);

    // A drafted character should normally already be a group member. Keep this
    // defensive path so {{char}} can still be included in both future wildcard
    // lists if SillyTavern exposes a temporarily inconsistent group snapshot.
    if (currentCharacter && !allCharacters.some(member => member.is_current_speaker)) {
        allCharacters.push(makeCharacterSnapshot(
            currentCharacter,
            context.characterId,
            disabledMembers,
            currentCharacter,
        ));
    }

    return {
        id: context.groupId,
        name: group.name ?? null,
        // Preserve the original avatar-ID fields for backward compatibility.
        members: memberAvatars,
        disabled_members: disabledMemberAvatars,
        all_characters: allCharacters,
        // The current speaker is always included as requested, even if a stale
        // SillyTavern snapshot still marks that character as muted.
        active_characters: allCharacters.filter(
            member => !member.muted || member.is_current_speaker,
        ),
    };
}

function makeMessageSnapshot(message, index) {
    if (!message) {
        return null;
    }

    return {
        index,
        name: message.name ?? null,
        role: message.is_user ? 'user' : (message.is_system ? 'system' : 'assistant'),
        text: message.mes ?? '',
        send_date: message.send_date ?? null,
        swipe_id: message.swipe_id ?? null,
        swipe_count: Array.isArray(message.swipes) ? message.swipes.length : 0,
        reasoning: message.extra?.reasoning ?? message.extra?.reasoning_content ?? null,
    };
}

function buildSnapshot(rawGenerationType = 'manual') {
    const context = SillyTavern.getContext();
    const character = getCurrentCharacter(context);
    const group = getCurrentGroup(context);
    const groupSnapshot = makeGroupSnapshot(context, group, character);
    const lastIndex = Array.isArray(context.chat) ? context.chat.length - 1 : -1;

    return {
        schema_version: 1,
        captured_at: new Date().toISOString(),
        source: 'sillytavern-brainengine-connector',
        generation: {
            type: normalizeGenerationType(rawGenerationType),
            raw_type: String(rawGenerationType ?? 'unknown'),
        },
        chat: {
            id: context.chatId ?? null,
            message_count: Array.isArray(context.chat) ? context.chat.length : 0,
            metadata: context.chatMetadata ?? {},
            last_message: lastIndex >= 0 ? makeMessageSnapshot(context.chat[lastIndex], lastIndex) : null,
        },
        character: character ? {
            index: context.characterId,
            name: character.name ?? character.data?.name ?? null,
            avatar: character.avatar ?? null,
            card: makeCharacterCardSnapshot(character),
        } : null,
        group: groupSnapshot,
        user: {
            name: context.name1 ?? null,
            persona: firstString(context.powerUserSettings?.persona_description),
        },
    };
}

async function sendSnapshot(rawGenerationType = 'manual', { silent = false } = {}) {
    const snapshot = buildSnapshot(rawGenerationType);
    const output = document.querySelector('#brainengine-connector-output');
    if (output) {
        output.value = JSON.stringify(snapshot, null, 2);
    }

    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), 5000);

    try {
        const response = await fetch(BRAINENGINE_CONTEXT_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(snapshot),
            signal: controller.signal,
        });

        if (!response.ok) {
            const detail = await response.text();
            throw new Error(`HTTP ${response.status}${detail ? `: ${detail}` : ''}`);
        }

        const result = await response.json();
        if (!silent) {
            setStatus(`送信成功：${result.generation_type} / ${result.received_at}`, 'success');
        }
        console.info(`[${MODULE_NAME}] Context sent`, result);
        return true;
    } catch (error) {
        const message = error.name === 'AbortError'
            ? 'Step-driven-MultiBrainEngineへの送信がタイムアウトしました'
            : `Step-driven-MultiBrainEngineへの送信に失敗しました：${error.message}`;
        console.warn(`[${MODULE_NAME}] ${message}`);
        if (!silent) {
            setStatus(message, 'error');
        }
        return false;
    } finally {
        window.clearTimeout(timeoutId);
    }
}

globalThis.brainEngineContextInterceptor = async function (_chat, _contextSize, _abort, type) {
    // This is supplemental metadata. Delivery failure must never stop the
    // user's normal generation request.
    await sendSnapshot(type, { silent: true });
};

function setStatus(message, kind = 'normal') {
    const status = document.querySelector('#brainengine-connector-status');
    if (!status) {
        return;
    }

    status.textContent = message;
    status.dataset.kind = kind;
}

function renderSnapshot() {
    const output = document.querySelector('#brainengine-connector-output');
    if (!output) {
        return;
    }

    try {
        const snapshot = buildSnapshot();
        output.value = JSON.stringify(snapshot, null, 2);
        const target = snapshot.character?.name ?? snapshot.group?.name ?? '未選択';
        setStatus(`取得成功：${target} / ${snapshot.chat.message_count}件のメッセージ`, 'success');
    } catch (error) {
        console.error(`[${MODULE_NAME}] Failed to read SillyTavern context`, error);
        output.value = '';
        setStatus(`取得失敗：${error.message}`, 'error');
    }
}

async function copySnapshot() {
    const output = document.querySelector('#brainengine-connector-output');
    if (!output?.value) {
        renderSnapshot();
    }

    try {
        await navigator.clipboard.writeText(output.value);
        setStatus('JSONをクリップボードへコピーしました', 'success');
    } catch (error) {
        console.error(`[${MODULE_NAME}] Failed to copy snapshot`, error);
        setStatus('コピーに失敗しました。テキスト欄から手動でコピーしてください', 'error');
    }
}

function createPanel() {
    const panel = document.createElement('div');
    panel.id = PANEL_ID;
    panel.className = 'brainengine-connector inline-drawer';
    panel.innerHTML = `
        <div class="inline-drawer-toggle inline-drawer-header">
            <b>S-MultiBrainConnecter</b>
            <div class="inline-drawer-icon fa-solid fa-circle-chevron-down down"></div>
        </div>
        <div class="inline-drawer-content brainengine-connector-content">
            <p class="brainengine-connector-description">
                生成開始時にチャット識別情報とキャラクターカードの情報をStep-driven-MultiBrainEngineへ自動送信します。手動送信で接続確認もできます。
            </p>
            <div class="brainengine-connector-actions">
                <button id="brainengine-connector-refresh" class="menu_button" type="button">現在の情報を取得</button>
                <button id="brainengine-connector-copy" class="menu_button" type="button">JSONをコピー</button>
                <button id="brainengine-connector-send" class="menu_button" type="button">Step-driven-MultiBrainEngineへ手動送信</button>
            </div>
            <div id="brainengine-connector-status" class="brainengine-connector-status" aria-live="polite">
                「現在の情報を取得」を押してください
            </div>
            <textarea id="brainengine-connector-output" readonly spellcheck="false" aria-label="Step-driven-MultiBrainEngine context JSON"></textarea>
        </div>
    `;

    panel.querySelector('#brainengine-connector-refresh').addEventListener('click', renderSnapshot);
    panel.querySelector('#brainengine-connector-copy').addEventListener('click', copySnapshot);
    panel.querySelector('#brainengine-connector-send').addEventListener('click', () => sendSnapshot('manual'));

    return panel;
}

function findSettingsContainer() {
    return document.querySelector('#extensions_settings2')
        ?? document.querySelector('#extensions_settings')
        ?? document.querySelector('.extensions_settings');
}

function initialize() {
    if (!globalThis.SillyTavern?.getContext) {
        console.error(`[${MODULE_NAME}] SillyTavern.getContext() is unavailable`);
        return;
    }

    if (document.querySelector(`#${PANEL_ID}`)) {
        return;
    }

    const settingsContainer = findSettingsContainer();
    if (!settingsContainer) {
        window.setTimeout(initialize, 500);
        return;
    }

    settingsContainer.append(createPanel());
    console.info(`[${MODULE_NAME}] Extension loaded`);
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialize, { once: true });
} else {
    initialize();
}
