"""Data-class policy for original model inputs, before projection or translation."""


def data_classes_allowed(payload, allowed_classes):
    if not isinstance(payload, dict):
        return False
    root_class = payload.get("data_class")
    if not isinstance(root_class, str) or root_class not in allowed_classes:
        return False
    pending, visited = [payload], set()
    while pending:
        value = pending.pop()
        if isinstance(value, (dict, list, tuple)):
            if id(value) in visited:
                continue
            visited.add(id(value))
        if isinstance(value, dict):
            if "data_class" in value:
                data_class = value["data_class"]
                if not isinstance(data_class, str) or data_class not in allowed_classes:
                    return False
            pending.extend(value.values())
        elif isinstance(value, (list, tuple)):
            pending.extend(value)
    return True
