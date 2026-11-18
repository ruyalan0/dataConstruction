"""Explicit bounded shoulder reorientation from public robot geometry."""
import numpy as np
from scipy.spatial.transform import Rotation


def validate_posture_action(action, phase):
    if set(action) != {'posture', 'steps'} or phase != 'transit':
        raise ValueError('Posture requires transit action:{posture:{left/right:upright},steps:1..6}')
    arms=action['posture']
    if not isinstance(arms,dict) or not arms or set(arms)-{'left','right'} or any(v!='upright' for v in arms.values()):
        raise ValueError('Posture arms must select upright')
    if type(action['steps']) is not int or not 1<=action['steps']<=6:
        raise ValueError('Posture requires 1..6 native steps')
    return list(arms),action['steps']


def upright_joint_target(model, joints, wrist):
    q=model.joints_array(joints).copy()
    base=model.observed_base(q,wrist)
    def height(angle):
        transform=base.copy()
        for i in range(2):
            rotation=np.eye(4)
            rotation[:3,:3]=Rotation.from_rotvec(model.axes[i]*(q[i] if i==0 else angle)).as_matrix()
            transform=transform@model.origins[i]@rotation
        return float((transform[:3,:3]@model.origins[2][:3,3])[2])
    offset=(height(0)+height(np.pi))/2
    cosine=height(0)-offset
    sine=height(np.pi/2)-offset
    if np.hypot(cosine,sine)<1e-8:
        raise ValueError('Shoulder cannot raise link2 in this measured base frame')
    best=np.arctan2(sine,cosine)
    candidates=[best+2*np.pi*k for k in range(-4,5) if model.lower[1]<=best+2*np.pi*k<=model.upper[1]]
    if not candidates:raise ValueError('No upright shoulder angle within limits')
    q[1]=min(candidates,key=lambda v:abs(v-q[1]))
    return q


def posture_increment(model,state,targets):
    solutions={};poses={}
    for arm in ('left','right'):
        q=model.joints_array(state[arm+'_arm_joint_state'])
        target=model.joints_array(targets[arm]) if arm in targets else q
        delta=np.clip(target-q,-.15,.15) if arm in targets else np.zeros(6)
        # Only the shoulder pitch changes; no hidden IK branch or wrist compensation.
        delta[np.arange(6)!=1]=0
        base=model.observed_base(q,state[arm+'_ee_pose'])
        # A rotation about joint2 moves the wrist on a circle. Bound its chord
        # analytically instead of silently retrying candidate motions.
        shoulder=base@model.origins[0]
        rotation=np.eye(4)
        rotation[:3,:3]=Rotation.from_rotvec(model.axes[0]*q[0]).as_matrix()
        shoulder=shoulder@rotation@model.origins[1]
        axis=shoulder[:3,:3]@model.axes[1]
        transform=base.copy();points=[]
        for origin,joint_axis,angle in zip(model.origins,model.axes,q):
            rotation=np.eye(4)
            rotation[:3,:3]=Rotation.from_rotvec(joint_axis*angle).as_matrix()
            transform=transform@origin@rotation
            points.append(transform[:3,3].copy())
        points.append(transform[:3,3]+transform[:3,:3]@np.array([.138,0,0]))
        radii=[]
        for point in points[2:]:
            vector=point-shoulder[:3,3]
            radii.append(np.linalg.norm(vector-axis*np.dot(vector,axis)))
        radius=max(radii)
        angle_limit=2*np.arcsin(min(1.,.024/(2*radius))) if radius>1e-12 else .15
        delta[1]=np.clip(delta[1],-angle_limit,angle_limit)
        candidate=q+delta
        predicted=base@model.forward(candidate)
        displacement=np.linalg.norm(predicted[:3,3]-np.asarray(state[arm+'_ee_pose'][:3]))
        if displacement>.024+1e-8:
            raise ValueError('Posture increment exceeds24mm wrist bound')
        solutions[arm]=candidate
        poses[arm]=np.r_[predicted[:3,3],Rotation.from_matrix(predicted[:3,:3]).as_quat()[[3,0,1,2]]]
    return solutions,poses


def posture_preview(model,state,arms,steps):
    targets={a:upright_joint_target(model,state[a+'_arm_joint_state'],state[a+'_ee_pose']) for a in arms}
    simulated={k:list(v) for k,v in state.items()}
    paths={a:[] for a in arms}
    for _ in range(steps):
        q,poses=posture_increment(model,simulated,targets)
        for arm in arms:
            paths[arm].append(poses[arm].tolist())
            simulated[arm+'_arm_joint_state']=q[arm].tolist()
            simulated[arm+'_ee_pose']=poses[arm].tolist()
    return {'target_joint2_rad':{a:float(q[1]) for a,q in targets.items()},'predicted_wrist_path':paths,
            'scope':'Public FK prediction only; <=24mm per-step motion of downstream joint origins, wrist and estimated fingertip; link meshes and collision clearance not modeled; no fixed-wrist guarantee'}
