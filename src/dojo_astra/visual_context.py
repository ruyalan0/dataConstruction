"""Bound transmitted images while retaining conversation text and archived RGB."""


def trim_images(messages, image_budget, protected=(), priority=()):
    if image_budget < 1:
        raise ValueError("Image budget must be positive")
    positions = [
        (mi, ci)
        for mi, message in enumerate(messages)
        if isinstance(message.get("content"), list)
        for ci, part in enumerate(message["content"])
        if part.get("type") == "image_url"
    ]
    pinned = [pos for pos in positions if any(messages[pos[0]]["content"][pos[1]] is p for p in protected)]
    if len(pinned) > image_budget:
        raise ValueError("Protected current views exceed image budget")
    preferred = [pos for pos in positions if pos not in pinned and
                 any(messages[pos[0]]["content"][pos[1]] is p for p in priority)]
    history = [pos for pos in positions if pos not in pinned and pos not in preferred]
    seen = {messages[mi]['content'][ci]['image_url']['url'] for mi, ci in pinned}
    unique = []
    for mi, ci in [*preferred, *reversed(history)]:
        url = messages[mi]['content'][ci]['image_url']['url']
        if url not in seen:
            unique.append((mi, ci))
            seen.add(url)
    slots = image_budget - len(pinned)
    kept = set(pinned + unique[:slots])
    removed = [pos for pos in positions if pos not in kept]
    for mi, ci in removed:
        messages[mi]["content"][ci] = {
            "type": "text",
            "text": "[Historical RGB omitted from request; original remains available via recall.]",
        }
    return {"images_sent": len(kept), "images_evicted": len(removed)}
