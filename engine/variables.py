"""Typed preset variables and atomic, permission checked Step updates."""

import copy
import json
import math
import re

TAG = "be-variable-update-v1"


def _finite_number(value):
    return type(value) is int or (type(value) is float and math.isfinite(value))


def validate_value(definition, value):
    kind = definition["type"]
    valid = {"integer": type(value) is int,
             "number": _finite_number(value),
             "string": isinstance(value, str), "boolean": type(value) is bool,
             "enum": isinstance(value, str) and value in definition.get("constraints", {}).get("values", [])}
    if not valid.get(kind, False):
        raise ValueError(f"{definition['id']}: invalid {kind} value")
    constraints = definition.get("constraints", {})
    if kind in ("integer", "number"):
        if ("min" in constraints and value < constraints["min"]) or ("max" in constraints and value > constraints["max"]):
            raise ValueError(f"{definition['id']}: value outside range")
    if kind == "string" and len(value) > constraints.get("max_length", 30000):
        raise ValueError(f"{definition['id']}: string too long")
    return value


def validate_definitions(definitions, steps):
    if definitions is None:
        definitions = []
    if not isinstance(definitions, list) or len(definitions) > 256:
        raise ValueError("variables must be a list of at most 256 definitions")
    ids, allowed = set(), {s["id"] for s in steps}
    result = []
    for raw in definitions:
        if not isinstance(raw, dict):
            raise ValueError("variable must be an object")
        item = copy.deepcopy(raw)
        identity = item.get("id")
        if not isinstance(identity, str) or not identity or len(identity) > 80 or identity in ids:
            raise ValueError("invalid or duplicate variable ID")
        ids.add(identity)
        if item.get("type") not in {"integer", "number", "string", "boolean", "enum"}:
            raise ValueError(f"{identity}: unsupported type")
        for key in ("name", "description"):
            if not isinstance(item.get(key, ""), str) or len(item.get(key, "")) > 30000:
                raise ValueError(f"{identity}: invalid {key}")
            item.setdefault(key, "")
        constraints = item.setdefault("constraints", {})
        if not isinstance(constraints, dict) or set(constraints) - {"min", "max", "max_length", "values"}:
            raise ValueError(f"{identity}: invalid constraints")
        for key in ("min", "max"):
            if key in constraints and not _finite_number(constraints[key]):
                raise ValueError(f"{identity}: nonfinite constraint")
        if constraints.get("min", -math.inf) > constraints.get("max", math.inf):
            raise ValueError(f"{identity}: reversed range")
        if "max_length" in constraints and (type(constraints["max_length"]) is not int or not 1 <= constraints["max_length"] <= 30000):
            raise ValueError(f"{identity}: invalid max_length")
        if item["type"] == "enum":
            choices = constraints.get("values")
            if not isinstance(choices, list) or not choices or any(not isinstance(v, str) for v in choices) or len(set(choices)) != len(choices):
                raise ValueError(f"{identity}: invalid enum values")
        for key in ("readable_by", "writable_by"):
            values = item.setdefault(key, [])
            targets = allowed | ({"writer"} if key == "readable_by" else set())
            if not isinstance(values, list) or any(not isinstance(v, str) or v not in targets for v in values):
                raise ValueError(f"{identity}: invalid {key}")
            item[key] = list(dict.fromkeys(values))
        validate_value(item, item.get("initial"))
        result.append(item)
    return result


def initial_values(definitions):
    return {item["id"]: copy.deepcopy(item["initial"]) for item in definitions}


def input_context(definitions, values, revision, agent_id, update_prompt=""):
    visible = [{"id": item["id"], "name": item["name"], "description": item["description"],
                "type": item["type"], "constraints": item["constraints"], "value": values[item["id"]],
                "writable": agent_id != "writer" and agent_id in item["writable_by"]}
               for item in definitions if agent_id in item["readable_by"]]
    write_only = [{"id": item["id"], "name": item["name"], "description": item["description"],
                   "type": item["type"], "constraints": item["constraints"]}
                  for item in definitions if agent_id != "writer" and agent_id in item["writable_by"] and agent_id not in item["readable_by"]]
    if not visible and not write_only:
        return ""
    payload = {"revision": revision, "variables": visible}
    if write_only:
        payload["writable_variables"] = write_only
    text = "[VARIABLES]\n" + json.dumps(payload, ensure_ascii=False) + "\n[/VARIABLES]"
    if write_only or any(item["writable"] for item in visible):
        text += (f'\n{update_prompt}\nFor authorized changes only, append one standalone terminal <{TAG}> block containing '
                 '{"base_revision":' + str(revision) + ',"updates":[{"id":"variable_id","op":"set","value":new_value}]}.'
                 f" Close with </{TAG}>. Omit the block if unchanged. This control block is an exception to the analysis-only rule.")
    return text


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON property in variable update")
        result[key] = value
    return result


def apply_output(output, definitions, values, revision, agent_id):
    """Return clean body, new values/revision and a diagnostic; reject all on any error."""
    marker = f"<{TAG}>"
    if marker not in output:
        return output, values, revision, None
    start = output.find(marker)
    # Examples in code fences and quoted inline text are ordinary analysis.
    if output[:start].count("```") % 2 or (start and output[start - 1] != "\n"):
        return output, values, revision, None
    body = output[:start].rstrip()
    try:
        tail = output[start:]
        if len(tail) > 1024 * 1024 or tail.count(marker) != 1 or not tail.rstrip().endswith(f"</{TAG}>"):
            raise ValueError("incomplete or multiple variable control blocks")
        payload = json.loads(tail[len(marker):].rstrip()[:-len(f"</{TAG}>")], object_pairs_hook=_unique_json_object)
        if not isinstance(payload, dict) or set(payload) != {"base_revision", "updates"}:
            raise ValueError("invalid variable control schema")
        if type(payload["base_revision"]) is not int or payload["base_revision"] != revision:
            raise ValueError("stale variable revision")
        updates = payload["updates"]
        if not isinstance(updates, list) or len(updates) > len(definitions) or agent_id == "writer":
            raise ValueError("invalid variable updates")
        catalog = {item["id"]: item for item in definitions}
        new_values, seen = copy.deepcopy(values), set()
        for update in updates:
            if not isinstance(update, dict) or set(update) != {"id", "op", "value"}:
                raise ValueError("invalid update schema")
            identity = update["id"]
            if not isinstance(identity, str) or identity in seen or identity not in catalog or update["op"] != "set":
                raise ValueError("unknown, duplicate variable or unsupported operation")
            item = catalog[identity]
            if agent_id not in item["writable_by"]:
                raise ValueError(f"{identity}: update permission denied")
            new_values[identity] = validate_value(item, update["value"])
            seen.add(identity)
        return body, new_values, revision + bool(updates), None
    except (ValueError, TypeError, KeyError) as exc:
        return body, values, revision, str(exc)
