"""Explicit camera/arm affordances; open fingers do not prove an empty hand."""
ARMS = ('left', 'right')
SKILL = 'idle-arm-observation'


def with_observer_skill(names):
    """Always supply the common coordination and temporary-observation rules."""
    return list(dict.fromkeys([*names, 'cross-side-handoff', SKILL]))


def observer_context(state, grips):
    arms = {}
    for arm in ARMS:
        measured = [float(value) for value in state[arm + '_gripper_measured_joints']]
        arms[arm] = dict(camera='cam_' + arm + '_wrist', gripper_command=float(grips[arm]),
            measured_finger_positions_m=measured,
            open_hand_candidate=bool(grips[arm] >= .9 and min(measured) >= .8 * .044),
            empty_hand_confirmed=False)
    return dict(available=True, tool='control_arms', arms=arms,
        instructions='Task operations take priority. Move a camera only for a specific '
        'blocking question unresolved by existing views/state. Observation is temporary; '
        'reassign both arms after obtaining evidence. After two failed acquisitions, '
        'compare changed grasp geometry, another task-valid target and switching arms. '
        'Use only a hand confirmed empty, idle and clear in RGB. '
        'Move that arm alone to an oblique noncontact view; set the working arm null '
        'and the observer gripper null to preserve its command. Inspect the next named wrist RGB. '
        'Open fingers alone do not prove emptiness or permission to move a supporting arm.')
