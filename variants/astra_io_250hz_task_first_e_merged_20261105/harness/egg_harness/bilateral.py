"""Compile both arm requests before one synchronous native segment."""
from .segments import compile_segment


def compile_motion(action, phase, obs, validate):
    if "arms" in action:
        if set(action)-{"arms","steps","settle"}:
            raise ValueError("Bilateral action mixes single-arm fields")
        arms=action["arms"]
        if not isinstance(arms,dict) or not arms or set(arms)-{"left","right"}:
            raise ValueError("Invalid arms")
        requests={}
        for arm,spec in arms.items():
            if not isinstance(spec,dict) or set(spec)-{"position","quaternion","gripper","toward"}:
                raise ValueError("Invalid arm specification")
            requests[arm]={**spec,"arm":arm,"steps":action["steps"],"settle":action.get("settle",0)}
    else:
        requests={action["arm"]:action}
    compiled={}
    for arm,request in requests.items():
        segment,goal=compile_segment(request,phase,obs)
        _,pos,q,steps,settle=validate(segment,phase,obs)
        compiled[arm]={"action":segment,"position":pos,"quaternion":q,"goal":goal}
    return compiled,steps,settle
