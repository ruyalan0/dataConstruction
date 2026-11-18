"""Closed-loop RGB policy. Persistent files are only read; traces are episode-local."""
import argparse
import json
import time
import traceback
from pathlib import Path
import numpy as np
from .client import Robot
from .geometry import unproject, rotation, quaternion
from .vision import Vision


class Policy:
    def __init__(self, config, episode, trace):
        self.trace = Path(trace); self.trace.mkdir(parents=True, exist_ok=False)
        self.skill = json.loads(Path(config['memory']).read_text())
        self.robot = Robot(episode, self.trace)
        self.vision = Vision(config, self.trace/'model')
        self.events = self.trace/'decisions.jsonl'

    def log(self, kind, **data):
        with self.events.open('a') as f:
            f.write(json.dumps(dict(kind=kind, step=self.robot.obs['step'], **data))+chr(10))
        print(kind, self.robot.obs['step'], json.dumps(data), flush=True)

    def perceive(self):
        r = self.vision.ask('Image is 640x480. Identify visible eggs not securely seated in a cup, including eggs perched on the holder rim, and all four cup centers of the holder. Return JSON {eggs:[{pixel:[u,v],surface:"basket"|"table"|"holder",major_axis_degrees:number}],slots:[{pixel:[u,v],row:"back"|"front",column:"left"|"right",occupied:bool}],lid_open:bool}. Image pixel coordinates must reference original image. Egg center is silhouette center; major axis angle clockwise from image horizontal. Back row is farther from robot, nearer hinge. Exclude eggs already seated in cups. Include rim-perched or stacked eggs as surface holder. Occluded centers must not be invented.', {'head':self.robot.obs['cameras']['cam_head']})
        if len(r['slots']) != 4: raise ValueError('Four slots not localized')
        self.log('perception', result=r)
        unknown=[e for e in r['eggs'] if e['pixel'] is None or any(v is None for v in e['pixel'])]
        if unknown: self.log('occluded_candidates',candidates=unknown)
        r['eggs']=[e for e in r['eggs'] if e not in unknown]
        return r

    def xyz(self, pixel, z):
        return unproject(pixel,z,self.skill['head_camera'])

    def pick(self, egg):
        rb=self.robot;s=self.skill
        z=s['basket_egg_center_z'] if egg['surface']=='basket' else s['table_egg_center_z']
        obj=self.xyz(egg['pixel'],z)
        # Closing direction is local Y. Align local Y with egg short axis.
        angle=np.deg2rad(egg['major_axis_degrees'])
        p2=np.array(egg['pixel'])+10*np.array([np.cos(angle),np.sin(angle)])
        delta=self.xyz(p2,z)-obj
        yaw=float(np.rad2deg(np.arctan2(delta[1],delta[0])))
        yaw=(yaw+90)%180-90  # Egg axis has 180-degree symmetry; avoid unreachable wrist flips.
        if rb.active_arm=='left': yaw += 180
        q=quaternion(rotation(90,yaw))
        if rb.active_arm=='left':
            rb.move('approach_position',[*obj[:2],1.10],quaternion(rotation(90,180)),.7,20,3)
            rb.move('approach_yaw',q=q,grip=.7,steps=24,settle=3)
        else:
            rb.move('approach',[*obj[:2],1.10],q,.7,14,1)
        rb.move('descend',[*obj[:2],z+s['grasp_offset']],q,.7,12)
        rb.move('close',grip=0,steps=1,settle=3)
        rb.move('lift',[*obj[:2],1.12],q,0,12)
        check=self.vision.ask(f'Is an egg securely held between the {rb.active_arm} gripper fingers, elevated above the basket/table? Use head and wrist RGB. Return {{held:bool,reason:string}}. Distinguish an egg below an empty gripper from one held. Do not infer success solely from gripper closure.',rb.obs['cameras'])
        self.log('grasp_check',result=check,gripper=rb.obs['state'][rb.active_arm+'_ee_joint_state'])
        if not check['held']:
            rb.move('failed_grasp_open',grip=1,steps=1)
            return False
        return True

    def place(self, slot):
        rb=self.robot;s=self.skill
        pose=np.array(rb.obs['state'][rb.active_arm+'_ee_pose'])
        from scipy.spatial.transform import Rotation
        current=Rotation.from_quat(pose[[4,5,6,3]])
        local=np.array([s['grasp_offset'],0,0])
        obj=pose[:3]+current.apply(local)
        tilted=rotation(s['place_pitch_degrees'],s['place_yaw_degrees'])
        q=quaternion(tilted)
        if rb.active_arm=='left':
            canonical=quaternion(rotation(90,180))
            rb.move('normalize_grasp_yaw',pose[:3],canonical,0,24,3)
            pose=np.array(rb.obs['state'][rb.active_arm+'_ee_pose'])
            obj=pose[:3]+Rotation.from_quat(pose[[4,5,6,3]]).apply(local)
        rb.move('tilt',obj-tilted.apply(local),q,0,24,3)
        target=self.xyz(slot['pixel'],s['holder_slot_plane_z'])
        target[2]=.97
        rb.move('transfer',target-tilted.apply(local),q,0,18)
        target[2]=s['back_release_z'] if slot['row']=='back' else s['front_release_z']
        rb.move('lower',target-tilted.apply(local),q,0,24,3)
        rb.move('release',grip=1,steps=1,settle=6)
        p=np.array(rb.obs['state'][rb.active_arm+'_ee_pose'][:3]);p += [0,-.08,.15]
        rb.move('retreat',p,q,1,12)
        rb.move('view_clear',[.34 if rb.active_arm=='right' else -.34,-.34,1.08],quaternion(rotation(90,180 if rb.active_arm=='left' else 0)),1,20,3)

    def nudge(self, egg, slot):
        rb=self.robot
        start=self.xyz(egg['pixel'],.82)
        target=self.xyz(slot['pixel'],self.skill['holder_slot_plane_z'])
        direction=target[:2]-start[:2]
        if np.linalg.norm(direction)<.005: raise RuntimeError('Ambiguous rim recovery direction')
        direction=direction/np.linalg.norm(direction)
        q=quaternion(rotation(55,90));r=rotation(55,90)
        contact=np.array([start[0]-direction[0]*.025,start[1]-direction[1]*.025,.842])
        rb.move('rim_above',contact-r.apply([.145,0,0])+[0,0,.10],q,0,18)
        rb.move('rim_contact',contact-r.apply([.145,0,0]),q,0,10)
        contact[:2]=target[:2];contact[2]=.832
        rb.move('rim_nudge',contact-r.apply([.145,0,0]),q,0,10,4)
        p=np.array(rb.obs['state'][rb.active_arm+'_ee_pose'][:3])+[0,-.08,.15]
        rb.move('rim_retreat',p,q,1,10)
        rb.move('view_clear',[.34 if rb.active_arm=='right' else -.34,-.34,1.08],quaternion(rotation(90,180 if rb.active_arm=='left' else 0)),1,20,3)

    def close(self, slots):
        rb=self.robot
        center=np.mean([self.xyz(x['pixel'],self.skill['holder_slot_plane_z']) for x in slots],axis=0)
        q=quaternion(rotation(90,0))
        rb.move('lid_front',[center[0],center[1]-.13,1.12],q,0,24,3)
        rb.move('lid_reachable_above',[center[0]+.04,center[1]+.065,1.105],q,0,20,3)
        rb.move('lid_push',[center[0],center[1]+.04,1.0],q,0,16,2)
        rb.move('lid_press',[center[0],center[1]-.015,.98],q,0,12,4)
        rb.move('lid_retreat',[center[0],center[1]-.05,1.12],q,1,12)
        for arm in ('right','left'):
            if rb.obs['ended']: break
            rb.move('home',rb.home[arm][:3],rb.home[arm][3:],1,14,arm=arm)

    def run(self):
        rb=self.robot
        # Clear basket occlusion before initial localization.
        rb.move('clear_view',[.40,-.36,1.12],quaternion(rotation(90,0)),1,14)
        cycles=0
        while not rb.obs['ended']:
            scene=self.perceive()
            empty=sorted([x for x in scene['slots'] if not x['occupied']],key=lambda x:(x['row']!='back',x['column']))
            if not empty:
                self.close(scene['slots']);break
            if rb.obs['step']>560: break
            if not scene['lid_open']: raise RuntimeError('Lid closed before all eggs seated')
            if not scene['eggs']: raise RuntimeError('No reachable visible egg')
            egg=min(scene['eggs'],key=lambda x:x['pixel'][0])
            z=self.skill['basket_egg_center_z'] if egg['surface']=='basket' else self.skill['table_egg_center_z']
            selected='left' if self.xyz(egg['pixel'],z)[0]<0 else 'right'
            if selected != rb.active_arm:
                old=rb.active_arm
                rb.move('park_previous_arm',rb.home[old][:3],rb.home[old][3:],1,20,3)
                rb.active_arm=selected
            self.log('cycle',egg=egg,slot=empty[0],arm=selected);cycles+=1
            if egg['surface']=='holder':
                self.nudge(egg,empty[0])
            elif self.pick(egg): self.place(empty[0])
            if cycles>=7: break
        while not rb.obs['ended']:
            rb.move('terminal_wait',steps=min(40,700-rb.obs['step']),settle=0)
        rb.command('finish')


def main():
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--episode',required=True);p.add_argument('--trace',required=True)
    a=p.parse_args();config=json.loads(Path(a.config).read_text());start=time.monotonic()
    policy=Policy(config,a.episode,a.trace)
    try:
        policy.run()
        result={'status':'native_end'}
    except BaseException:
        result={'status':'failure','traceback':traceback.format_exc()}
        raise
    finally:
        result.update(seconds=time.monotonic()-start,model_calls=policy.vision.calls,steps=policy.robot.obs['step'],human_interventions=0)
        (Path(a.trace)/'policy-result.json').write_text(json.dumps(result,indent=2))

if __name__=='__main__': main()
