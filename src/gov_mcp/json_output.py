"""Local JSON contracts. Never repair, coerce or discard a model's fields."""

import json
import math

from pydantic import BaseModel, ConfigDict


class StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


def exact_json_equal(left, right):
    """Compare JSON values without Python's bool/int or int/float equivalence.

    Only builtin JSON container and scalar types are accepted. Reject cycles,
    nonfinite floats and non-string object keys, including in persisted values.
    Strings and numbers are never normalized or repaired.
    """
    active = set()

    def equal(a, b):
        kind = type(a)
        if kind is not type(b):
            return False
        if kind in (str, bool, int, type(None)):
            return a == b
        if kind is float:
            return math.isfinite(a) and math.isfinite(b) and a == b
        if kind not in (dict, list):
            return False
        pair = (id(a), id(b))
        if pair in active:
            return False
        active.add(pair)
        try:
            if kind is dict:
                return (all(type(key) is str for key in a)
                        and all(type(key) is str for key in b)
                        and a.keys() == b.keys()
                        and all(equal(a[key], b[key]) for key in a))
            return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
        finally:
            active.remove(pair)

    try:
        return equal(left, right)
    except RecursionError:
        return False


def object_from_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValueError("NONFINITE_JSON_NUMBER")

    def finite_float(raw_number):
        value = float(raw_number)
        if not math.isfinite(value):
            raise ValueError("NONFINITE_JSON_NUMBER")
        return value

    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant,
                       parse_float=finite_float)
    if not isinstance(value, dict):
        raise ValueError("JSON_OBJECT_REQUIRED")
    return value


def contract_messages(messages, response_model):
    """The schema is trusted application configuration, never an input schema."""
    if not isinstance(response_model, type) or not issubclass(response_model, StrictOutput):
        raise ValueError("INVALID_OUTPUT_CONTRACT")
    instruction = (
        "\nOutput contract: return only a JSON instance matching the following JSON Schema. "
        "All required keys must be present; do not add keys. "
        "Do not output the schema itself or copy API response_format metadata into the answer. "
        "In particular, type/json_object is protocol metadata, not an answer field unless "
        "explicitly listed in properties. This contract specifies format, not factual truth; "
        "retain unknowns, evidence, negation and pauses required by the task.\n"
        + json.dumps(response_model.model_json_schema(by_alias=True), ensure_ascii=False,
                     allow_nan=False, separators=(",", ":")))
    copied = [dict(message) for message in messages]
    for message in copied:
        if message.get("role") == "system" and isinstance(message.get("content"), str):
            message["content"] += instruction
            break
    else:
        copied.insert(0, {"role": "system", "content": instruction})
    return copied


def validate_contract(value, response_model):
    # Return the original object: validation must not silently normalize a model
    # response or turn an invalid historical observation into a passing one.
    response_model.model_validate(value, strict=True, extra="forbid")
    return value
