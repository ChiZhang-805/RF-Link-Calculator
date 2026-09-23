from copy import deepcopy
from uuid import uuid4


def edit_structure(
    draft: dict, action: str, stage_id: str | None = None, new_stage: dict | None = None
) -> dict:
    """Stable IDs survive structural edits; copies always receive a new ID."""
    d = deepcopy(draft)
    stages = list(d["stages"])
    index = next((i for i, s in enumerate(stages) if s["id"] == stage_id), None)
    if action in ("add", "copy") and len(stages) >= 200:
        raise ValueError("最多200级器件")
    if action == "add":
        if new_stage is None:
            raise ValueError("需要新器件参数")
        stages.append(deepcopy(new_stage))
    elif index is None:
        raise ValueError("器件ID不存在")
    elif action == "copy":
        s = deepcopy(stages[index])
        s["id"] = str(uuid4())
        s["name"] += "（副本）"
        stages.insert(index + 1, s)
    elif action == "delete":
        stages.pop(index)
    elif action == "up" and index > 0:
        stages[index - 1], stages[index] = stages[index], stages[index - 1]
    elif action == "down" and index < len(stages) - 1:
        stages[index + 1], stages[index] = stages[index], stages[index + 1]
    for i, s in enumerate(stages, 1):
        s["order"] = i
    d["stages"] = stages
    return d
