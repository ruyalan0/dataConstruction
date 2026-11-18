"""Resolve the predeclared task split, excluding development-exposed cases."""
def heldout_layouts(curriculum, task):
    layouts = curriculum.get("heldout_layouts_by_task", {}).get(task, curriculum["heldout_layouts"])
    if len(layouts) != 3 or any(type(index) is not int or index < 0 for index in layouts) or len(set(layouts)) != 3:
        raise ValueError("Three distinct nonnegative heldout layouts required")
    exposed = set(curriculum["development_layouts"]) | set(curriculum.get("development_exposed_layouts_by_task", {}).get(task, []))
    if exposed.intersection(layouts):
        raise ValueError("Heldout layouts overlap development-exposed cases")
    return layouts
