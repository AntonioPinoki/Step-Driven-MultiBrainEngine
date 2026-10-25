"""Opt-in prompt contract for generators sharing a three-item selector.

The marker belongs to the saved prompt. No model output is repaired or removed.
Unmarked prompts and malformed selector records keep the legacy message layout.
"""
import re

MARKER = re.compile(r'^<!-- BE_ROUTE: (CHARACTER|SCENE) -->\s*')
TAG_ROUTES = {
    'CHARACTER_DIALOGUE': 'CHARACTER',
    'CHARACTER_ACTION': 'CHARACTER',
    'CHARACTER_REACTION': 'CHARACTER',
    'SCENE_ENVIRONMENT': 'SCENE',
    'SCENE_SENSORY': 'SCENE',
}


def prepare_routing_contract(prompt, prior_context):
    marker = MARKER.match(prompt)
    if not marker:
        return prompt, ''
    prompt = prompt[marker.end():]
    selectors = re.findall(
        r'<roleplaycontentselector>\s*(.*?)\s*</roleplaycontentselector>',
        prior_context, re.S)
    if len(selectors) != 1 or '### SELECTED ITEMS' not in selectors[0]:
        return prompt, ''
    selected = selectors[0].split('### SELECTED ITEMS', 1)[1]
    blocks = re.split(r'(?m)^\s*- ORDER:\s*', selected)[1:]
    records = []
    for block in blocks:
        order = re.match(r'(\d+)\s*\n', block)
        route = re.search(r'(?m)^- ROUTE: (CHARACTER|SCENE)\s*$', block)
        tag = re.search(r'(?m)^- TAG: ([A-Z_]+)\s*$', block)
        if not order or not route or not tag:
            return prompt, ''
        if TAG_ROUTES.get(tag[1]) != route[1]:
            return prompt, ''
        records.append((int(order[1]), route[1]))
    if [number for number, _ in records] != [1, 2, 3]:
        return prompt, ''
    route = marker[1]
    assigned = [number for number, value in records if value == route]
    contract = ('\n\n[CURRENT ROUTE ASSIGNMENT]\n担当ROUTE: ' + route
                + '\n今回出力するORDER一覧: '
                + (', '.join(map(str, assigned)) if assigned else '空（0件）') + '\n')
    contract += (
        'この一覧の各ORDERについて、元のCONTENTを一度ずつ具体化する。一覧にないORDERは出さない。元の番号を保ち、一覧の昇順で出力する。'
        if assigned else
        '今回の担当は0件。回答全文は「なし」の2文字だけ。ORDERやCONTENTや箇条書きは出さない。')
    return prompt, contract
