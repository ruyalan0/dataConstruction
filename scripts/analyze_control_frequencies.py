"""Compare saved 25/250 Hz episodes on a shared 40 ms observation grid."""
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / 'runs/general_pickup-io-25hz-3layouts-20261019-183200/general_pickup'
TIMED = ROOT / 'runs/general_pickup-io-250hz-timed-3layouts-20261020-090459/general_pickup'
L20 = ROOT / 'runs/general_pickup-io-250hz-timed-20261020-084923'
HISTORICAL = ROOT / 'runs/ten-tasks-control-only-20261014-001220'
ARMS = ('left', 'right')


def read(path):
    return json.loads(path.read_text())


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def distribution(values):
    values = np.asarray(values, dtype=float)
    assert len(values) and np.isfinite(values).all()
    return dict(count=len(values), mean=float(np.mean(values)), rms=float(np.sqrt(np.mean(values**2))),
                p50=float(np.percentile(values, 50)), p95=float(np.percentile(values, 95)),
                maximum=float(np.max(values)))


def load_episode(run, hz, layout, sources):
    result = read(run / 'results.json')[0]
    episode, model = run / f'seed-{layout}', run / f'policy-{layout}/model'
    factor = hz // 25
    assert result['status'] == 'native_end' and result['official_observation']['valid_sample']
    assert not result['official_observation']['unstable']
    assert result['policy_returncode'] == result['simulator_returncode'] == 0
    timing = read(episode / 'control-timing.json')
    assert timing['action_hz'] == hz and timing['physics_hz'] == 250
    frame_paths = [episode / 'frames/initial.json'] + [
        episode / f'frames/step-{step:04d}.json' for step in range(factor, result['action_count']+1, factor)]
    frames = [read(path) for path in frame_paths]
    assert [frame['step'] for frame in frames] == list(range(0, result['action_count']+1, factor))
    feedback = rows(episode / 'control-feedback.jsonl')
    actions = rows(episode / 'actions.jsonl')
    tracking = [row for call in feedback for row in call['tracking']]
    requests = [read(path) for path in sorted(model.glob('*.request.json'))]
    contexts = [json.loads(request['question']) for request in requests]
    assert len(requests) == len(feedback) == result['policy']['model_calls']
    assert len(actions) == result['action_count'] == sum(call['executed_steps'] for call in feedback)
    assert [action['step'] for action in actions] == list(range(1, len(actions)+1))
    for name in ('results.json', 'layout-provenance.json', 'launch-record.json'):
        sources.add(run / name)
    sources.update(frame_paths)
    sources.update(episode / name for name in ('control-timing.json','actions.jsonl','control-feedback.jsonl'))
    sources.update(path for path in (episode / 'interruptions.jsonl', model / 'api-retries.jsonl') if path.is_file())
    sources.update(path for path in model.glob('*.json') if path.name.endswith(
        ('.request.json','.response.json','.cost.json','.tool-result.json')))
    arm_metrics, series = {}, {}
    for arm in ARMS:
        own_tracking = [row for row in tracking if row['arm'] == arm]
        common_tracking = [row for row in own_tracking if row['step'] % factor == 0]
        q = np.asarray([frame['state'][arm+'_arm_joint_state'] for frame in frames])
        pos = np.asarray([frame['state'][arm+'_ee_pose'][:3] for frame in frames])
        fingers = np.asarray([frame['state'][arm+'_gripper_measured_joints'][0] for frame in frames])
        assert np.isfinite(q).all() and np.isfinite(pos).all() and np.isfinite(fingers).all()
        velocity = np.diff(pos, axis=0) / .04
        speed = np.linalg.norm(velocity, axis=1)
        acceleration = np.diff(velocity, axis=0) / .04
        jerk = np.diff(acceleration, axis=0) / .04
        joint_velocity = np.diff(q, axis=0) / .04
        path = float(np.sum(np.linalg.norm(np.diff(pos, axis=0), axis=1)))
        displacement = float(np.linalg.norm(pos[-1]-pos[0]))
        limits = np.asarray([joint['limits_rad'] for joint in contexts[0]['joint_semantics']['arms'][arm]])
        outside = (q < limits[:,0]) | (q > limits[:,1])
        arm_metrics[arm] = dict(
            path_length_m=path, displacement_m=displacement,
            displacement_over_path=displacement/path if path > 1e-8 else None,
            max_displacement_from_start_m=float(np.linalg.norm(pos-pos[0],axis=1).max()),
            speed_m_s=distribution(speed), acceleration_m_s2=distribution(np.linalg.norm(acceleration,axis=1)),
            jerk_m_s3=distribution(np.linalg.norm(jerk,axis=1)),
            absolute_joint_speed_rad_s=distribution(np.abs(joint_velocity).ravel()),
            maximum_joint_change_per_40ms_rad=float(np.max(np.abs(np.diff(q,axis=0)))),
            stationary_fraction_40ms=float(np.mean((speed < .005) & (np.max(np.abs(joint_velocity),axis=1) < .03))),
            measured_joint_limit_violations=int(outside.sum()),
            wrist_error_native_mm=distribution([row['wrist_error_m']*1000 for row in own_tracking]),
            wrist_error_common_40ms_mm=distribution([row['wrist_error_m']*1000 for row in common_tracking]),
            joint_error_native_rad=distribution([row['joint_error_rad'] for row in own_tracking]),
            joint_error_common_40ms_rad=distribution([row['joint_error_rad'] for row in common_tracking]),
            wrist_tracking_threshold_exceedances=sum(row['wrist_error_m'] > .035 for row in own_tracking))
        series[arm] = dict(t=(np.arange(len(frames))*.04).tolist(), xyz=pos.tolist(), joints=q.tolist(),
                           finger_m=fingers.tolist(), speed_m_s=speed.tolist(),
                           tracking_t=[row['step']/hz for row in common_tracking],
                           wrist_error_mm=[row['wrist_error_m']*1000 for row in common_tracking])
    active = max(ARMS, key=lambda arm: arm_metrics[arm]['path_length_m'])
    costs = [read(path) for path in sorted(model.glob('*.cost.json'))]
    commands = rows(run / f'policy-{layout}/commands.jsonl')[1:]
    assert len(commands) == len(feedback)
    rounds, closure_transitions, previous_grip = [], Counter(), {arm:1. for arm in ARMS}
    for index, (command, call) in enumerate(zip(commands, feedback)):
        action = command['command']['action']
        requested = action['steps']+action['settle']
        for arm, spec in action['arms'].items():
            grip = spec.get('gripper', previous_grip[arm])
            if grip <= .05 and previous_grip[arm] > .05:
                closure_transitions[arm] += 1
            previous_grip[arm] = grip
        rounds.append(dict(decision=index+1, start_step=requests[index]['step'], end_step=call['step'],
                           requested_ticks=requested, executed_ticks=call['executed_steps'],
                           requested_seconds=requested/hz, executed_seconds=call['executed_steps']/hz,
                           status=call['status'], active_controls=call['controls']))
    interruptions = rows(episode/'interruptions.jsonl')
    interruption_details = []
    for event in interruptions:
        index = next(i for i, command in enumerate(commands) if command['command']['id'] == event['command'])
        before = contexts[index]['state'][event['arm']+'_gripper_measured_joints'][0]
        if index+1 < len(contexts):
            after = contexts[index+1]['state'][event['arm']+'_gripper_measured_joints'][0]
        else:
            after = frames[-1]['state'][event['arm']+'_gripper_measured_joints'][0]
        interruption_details.append(dict(event, decision=index+1, simulation_seconds=event['step']/hz,
            finger_signed_change_mm=(after-before)*1000, wrist_threshold_exceeded=event['wrist_error_m'] > .035,
            finger_threshold_exceeded=event['opening_change_m'] > .003))
    question_bytes = sum(len(request['question'].encode()) for request in requests)
    recent_bytes = sum(len(json.dumps(context['recent_executed_segments'], ensure_ascii=False).encode()) for context in contexts)
    statuses = Counter(call['status'] for call in feedback)
    result_row = dict(hz=hz, layout=layout, run=str(run), active_arm=active, task=result['task'],
        success=result['success'], score=result['official_observation']['score'],
        layout_sha256=read(run/'layout-provenance.json')['sha256'],
        task_horizon_seconds=8, simulation_seconds=result['action_count']/hz,
        native_action_count=result['action_count'], model_calls=result['policy']['model_calls'],
        invalid_actions=result['policy']['invalid_actions'], execution_statuses=dict(statuses),
        kinematic_rejections=statuses['kinematic_rejection'], pre_motion_rejections=statuses['rejected_before_motion'],
        protection_interruptions=statuses['tracking_or_gripper_change'], interruption_details=interruption_details,
        gripper_close_command_transitions=dict(closure_transitions),
        wall_seconds=result['wall_seconds'], policy_wall_seconds=result['policy']['seconds'],
        model_request_seconds=sum(cost['seconds'] for cost in costs),
        input_tokens=sum(cost['usage']['input_tokens'] for cost in costs),
        output_tokens=sum(cost['usage']['output_tokens'] for cost in costs),
        current_question_bytes=question_bytes, current_recent_segments_bytes=recent_bytes,
        current_recent_segments_fraction=recent_bytes/question_bytes,
        api_retry_attempts=sum(row['event']=='retry' for row in rows(model/'api-retries.jsonl')),
        arms=arm_metrics, rounds=rounds, series=series)
    return result_row, frames


def historical_tasks(sources):
    tasks=[]
    for path in sorted(HISTORICAL.glob('*/L20/results.json')):
        result=read(path)[0]
        feedback=rows(path.parent/'seed-20/control-feedback.jsonl')
        statuses=Counter(call['status'] for call in feedback)
        sources.add(path)
        if (path.parent/'seed-20/control-feedback.jsonl').is_file():
            sources.add(path.parent/'seed-20/control-feedback.jsonl')
        tasks.append(dict(task=result['task'], layout=20, success=result['success'],
                         score=result['official_observation']['score'], invalid_actions=result['policy']['invalid_actions'],
                         model_calls=result['policy']['model_calls'], execution_statuses=dict(statuses),
                         paired_250hz_available=False, io_version='historical control-only, before 2026-10-19 I/O changes',
                         source=str(path)))
    return tasks


def analyze(output):
    output.mkdir(parents=True,exist_ok=True)
    sources={Path(__file__)}
    episodes, frame_sets = [], {}
    for hz in (25,250):
        for layout in (20,21,22):
            run=BASELINE/f'L{layout}' if hz==25 else (L20 if layout==20 else TIMED/f'L{layout}')
            episode, frames=load_episode(run,hz,layout,sources)
            episodes.append(episode);frame_sets[hz,layout]=frames
    for layout in (20,21,22):
        pair=[row for row in episodes if row['layout']==layout]
        assert pair[0]['layout_sha256']==pair[1]['layout_sha256']
    aggregate={}
    for hz in (25,250):
        own=[row for row in episodes if row['hz']==hz]
        aggregate[str(hz)]=dict(episodes=len(own),successes=sum(row['success'] for row in own),
            success_rate=sum(row['success'] for row in own)/len(own), mean_score=float(np.mean([row['score'] for row in own])),
            **{key:sum(row[key] for row in own) for key in ('simulation_seconds','model_calls','invalid_actions',
                'kinematic_rejections','pre_motion_rejections','protection_interruptions','wall_seconds',
                'policy_wall_seconds','model_request_seconds','input_tokens','output_tokens','api_retry_attempts',
                'current_question_bytes','current_recent_segments_bytes')})
        aggregate[str(hz)]['protection_interruption_rate']=aggregate[str(hz)]['protection_interruptions']/aggregate[str(hz)]['model_calls']
    data=dict(created_at=datetime.now(timezone.utc).isoformat(), task='general_pickup',
              common_observation_interval_seconds=.04, episodes=episodes, aggregate=aggregate,
              historical_25hz_other_tasks=historical_tasks(sources))
    save(output/'metrics.json',data)
    save(output/'source-sha256.json',{str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(sources)})
    with (output/'paired-metrics.csv').open('w') as stream:
        names=['hz','layout','active_arm','success','score','simulation_seconds','model_calls','invalid_actions',
               'protection_interruptions','kinematic_rejections','wall_seconds','model_request_seconds','input_tokens',
               'wrist_error_p95_mm','wrist_error_max_mm','path_m','speed_max_m_s','acceleration_rms_m_s2']
        writer=csv.DictWriter(stream,fieldnames=names);writer.writeheader()
        for row in episodes:
            arm=row['arms'][row['active_arm']]
            writer.writerow(dict({key:row[key] for key in names if key in row},
                wrist_error_p95_mm=arm['wrist_error_common_40ms_mm']['p95'],
                wrist_error_max_mm=arm['wrist_error_common_40ms_mm']['maximum'],path_m=arm['path_length_m'],
                speed_max_m_s=arm['speed_m_s']['maximum'],acceleration_rms_m_s2=arm['acceleration_m_s2']['rms']))
    return data,frame_sets


def visualizations(output, data, frame_sets, videos):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from PIL import Image, ImageDraw, ImageFont
    colors={25:'#2463af',250:'#e07a18'}
    lookup={(row['hz'],row['layout']):row for row in data['episodes']}
    layouts=(20,21,22)
    fig,axes=plt.subplots(2,3,figsize=(14,7.5),layout='constrained')
    panels=[('Simulation time (s)',lambda r:r['simulation_seconds']),
            ('Model decisions',lambda r:r['model_calls']),
            ('Wrist tracking error P95 (mm, 40 ms grid)',lambda r:r['arms'][r['active_arm']]['wrist_error_common_40ms_mm']['p95']),
            ('Wrist tracking error max (mm, 40 ms grid)',lambda r:r['arms'][r['active_arm']]['wrist_error_common_40ms_mm']['maximum']),
            ('Measured wrist path length (m, 40 ms grid)',lambda r:r['arms'][r['active_arm']]['path_length_m']),
            ('Input tokens (millions, provider reported)',lambda r:r['input_tokens']/1e6)]
    for ax,(title,value) in zip(axes.flat,panels):
        for i,hz in enumerate((25,250)):
            bars=ax.bar(np.arange(3)+(i-.5)*.35,[value(lookup[hz,l]) for l in layouts],width=.35,
                        color=colors[hz],label=f'{hz} Hz')
            ax.bar_label(bars,fmt='%.2f',fontsize=8,padding=3)
        ax.set_xticks(range(3),[f'L{l}' for l in layouts]);ax.set_title(title,fontsize=10)
        ax.margins(y=.18);ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
    axes[0,0].legend()
    fig.suptitle('general_pickup: one paired run per layout; both groups succeeded 3/3',fontsize=13)
    fig.savefig(output/'overview.png',dpi=180);fig.savefig(output/'overview.svg');plt.close(fig)
    fig,axes=plt.subplots(4,3,figsize=(14,10),layout='constrained')
    for col,l in enumerate(layouts):
        for hz in (25,250):
            row=lookup[hz,l];s=row['series'][row['active_arm']];t=np.array(s['t'])
            axes[0,col].plot(t,np.array(s['xyz'])[:,2],color=colors[hz],label=f'{hz} Hz')
            axes[1,col].plot(t[1:],s['speed_m_s'],color=colors[hz])
            axes[2,col].plot(s['tracking_t'],s['wrist_error_mm'],color=colors[hz])
            axes[3,col].plot(t,np.array(s['finger_m'])*1000,color=colors[hz])
            for event in row['interruption_details']:
                for ax in axes[:,col]:ax.axvline(event['simulation_seconds'],color=colors[hz],ls=':',alpha=.4)
        axes[0,col].set_title(f'L{l} ({lookup[25,l]["active_arm"]} arm)')
        axes[3,col].set_xlabel('Simulation time (s)')
        for ax in axes[:,col]:ax.grid(alpha=.2)
    for ax,label in zip(axes[:,0],('Measured wrist Z (m)','Wrist speed (m/s)','Tracking residual (mm)','First finger position (mm)')):
        ax.set_ylabel(label)
    axes[0,0].legend()
    fig.suptitle('Measured motion at 40 ms; dotted lines mark gripper/tracking protection events',fontsize=12)
    fig.savefig(output/'motion.png',dpi=180);fig.savefig(output/'motion.svg');plt.close(fig)
    font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',18)
    small=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',15)
    image_sources=set()

    def opened(frame,camera,size):
        path=Path(frame['cameras'][camera]);image_sources.add(path)
        with Image.open(path) as source:return source.convert('RGB').resize(size,Image.Resampling.LANCZOS)

    def panel(frame,hz,layout,index):
        result=lookup[hz,layout]
        canvas=Image.new('RGB',(720,400),'#17212f')
        canvas.paste(opened(frame,'cam_head',(480,360)),(0,40))
        canvas.paste(opened(frame,'cam_left_wrist',(240,180)),(480,40))
        canvas.paste(opened(frame,'cam_right_wrist',(240,180)),(480,220))
        draw=ImageDraw.Draw(canvas)
        done=index>=len(frame_sets[hz,layout])-1
        draw.text((8,10),f'{hz} Hz | L{layout} | t={frame["step"]/hz:.2f}s | {result["active_arm"]} arm'+
                  (' | SUCCESS' if done else ''),font=font,fill='white')
        for xy,label in (((8,48),'HEAD'),((488,48),'LEFT WRIST'),((488,228),'RIGHT WRIST')):
            draw.rectangle((xy[0]-3,xy[1]-2,xy[0]+130,xy[1]+21),fill='#17212f')
            draw.text(xy,label,font=small,fill='white')
        return canvas

    for layout in layouts:
        count=max(len(frame_sets[hz,layout]) for hz in (25,250))
        sample_indices=np.rint(np.linspace(0,count-1,4)).astype(int)
        sheet=Image.new('RGB',(1440,800),'white')
        for row,hz in enumerate((25,250)):
            for col,index in enumerate(sample_indices):
                own=min(index,len(frame_sets[hz,layout])-1)
                picture=panel(frame_sets[hz,layout][own],hz,layout,own).resize((360,200),Image.Resampling.LANCZOS)
                sheet.paste(picture,(col*360,row*400))
                # A larger active-wrist view makes grasp alignment readable.
                wrist=opened(frame_sets[hz,layout][own],'cam_'+lookup[hz,layout]['active_arm']+'_wrist',(267,200))
                sheet.paste(wrist,(col*360+46,row*400+200))
        sheet.save(output/f'L{layout}-contact-sheet.jpg',quality=94)
        if videos:
            command=['ffmpeg','-nostdin','-n','-v','error','-f','rawvideo','-pix_fmt','rgb24','-s','1440x400',
                     '-r','25','-i','-','-an','-c:v','libx264','-threads','2','-crf','20','-pix_fmt','yuv420p',
                     '-movflags','+faststart',str(output/f'L{layout}-comparison.mp4')]
            with (output/f'L{layout}-video.log').open('w') as log:
                process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT)
                for index in range(count):
                    joined=Image.new('RGB',(1440,400))
                    for col,hz in enumerate((25,250)):
                        own=min(index,len(frame_sets[hz,layout])-1)
                        joined.paste(panel(frame_sets[hz,layout][own],hz,layout,own),(col*720,0))
                    process.stdin.write(joined.tobytes())
                process.stdin.close()
                if process.wait()!=0:raise RuntimeError('Comparison video encoding failed')
    # Event views use their own simulation times, rather than matched elapsed time.
    sheet=Image.new('RGB',(1920,1040),'#17212f')
    for row,hz in enumerate((25,250)):
        result=lookup[hz,20];run=Path(result['run'])
        events=result['interruption_details']
        marks=[result['rounds'][events[0]['decision']-1]['start_step'],events[-1]['step'],result['native_action_count']]
        for col,step in enumerate(marks):
            frame_path=run/f'seed-20/frames/step-{step:04d}.json'
            if frame_path.is_file():frame=read(frame_path)
            else:
                request=next(read(p) for p in sorted((run/'policy-20/model').glob('*.request.json')) if read(p)['step']==step)
                head=Path(request['images'][0]['path'])
                frame=read(head.with_name(head.name.removesuffix('-cam_head.jpg')+'.json'))
            sheet.paste(opened(frame,'cam_right_wrist',(640,480)),(col*640,row*520+40))
            draw=ImageDraw.Draw(sheet)
            labels=('before interrupted lift','after protection events','final successful grasp')
            draw.text((col*640+10,row*520+10),f'{hz} Hz | {labels[col]} | t={step/hz:.3f}s',font=font,fill='white')
    sheet.save(output/'L20-grasp-events.jpg',quality=95)
    hashes=read(output/'source-sha256.json')
    hashes.update({str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(image_sources)})
    save(output/'source-sha256.json',hashes)
    return dict(image_sources_hashed=len(image_sources),comparison_video_fps=25,
                comparison_video_timing='matched simulation time; successful side freezes after native termination')


def write_report(output,data):
    lookup={(row['hz'],row['layout']):row for row in data['episodes']}
    a,b=data['aggregate']['25'],data['aggregate']['250']
    lines=['# 25 Hz 与 250 Hz 控制对比','',
        '**配对 general_pickup 结果：两组均 3/3 成功、均分 100；当前没有成功率提升的证据。** '
        '250 Hz 的这些轨迹在 40 ms 网格上更平缓，但任务完成更慢、输入 token 明显增加。','',
        '## 比较范围','',
        '主比较使用 2026-10-19 的 astra_io_25hz L20/L21/L22 与 2026-10-20 的 '
        'astra_io_250hz_timed 对应三局。layout 文件 SHA-256 逐对一致；模型均为 gpt-6-astra / high，'
        '仅 control_arms，保留 memory、历史回放、四位小数和无 phase 输出。两组物理步长均为 4 ms，'
        '任务时钟均为 25 Hz、预算均为 8 秒。','',
        '此前 astra_io_250hz 的 L20 失败局只有 0.8 秒预算；它属于已发现并修正的时间配置问题，'
        '未计入当前 250 Hz 成功率。初始化失败且没有执行策略的尝试也未计入。','',
        '其余十任务只有较早的 25 Hz control-only 结果，且 I/O 版本不同，没有对应 250 Hz 实跑。'
        '下表保留这些任务的缺测状态，不将不同任务的历史成绩混为跨频率实验。','',
        '## 逐任务成功率与无效动作','',
        '| 任务 | 25 Hz 成功 | 250 Hz 成功 | 25 Hz 分数 | 原 invalid_actions | 运动学中断 | 跟踪/夹爪保护中断 |',
        '|---|---:|---:|---:|---:|---:|---:|',
        '| general_pickup（配对三布局） | 3/3 | 3/3 | 100 均分 | 0 | 0 | 2（250 Hz 为 3） |']
    for task in data['historical_25hz_other_tasks']:
        statuses=task['execution_statuses']
        lines.append(f"| {task['task']}（历史 L20） | {int(task['success'])}/1 | 未测 | {task['score']:g} | "
                     f"{task['invalid_actions']} | {statuses.get('kinematic_rejection',0)} | "
                     f"{statuses.get('tracking_or_gripper_change',0)} |")
    historical=data['historical_25hz_other_tasks']
    kin=sum(r['execution_statuses'].get('kinematic_rejection',0) for r in historical)
    guard=sum(r['execution_statuses'].get('tracking_or_gripper_change',0) for r in historical)
    lines+=['',f'历史十任务成功 {sum(r["success"] for r in historical)}/10，原 invalid_actions 合计 '
        f'{sum(r["invalid_actions"] for r in historical)}；另有 {kin} 次运动学中断、{guard} 次跟踪/夹爪保护中断。'
        '12 次运动学中断均执行过 1..4 个动作步，原计数没有将其计入 invalid_actions。','',
        '## 配对三局结果','',
        '| Layout / 目标 | 25 Hz 分数 | 250 Hz 分数 | 仿真秒 25→250 | 模型调用 25→250 | 原无效动作 25→250 | 保护中断 25→250 |',
        '|---|---:|---:|---:|---:|---:|---:|']
    targets={20:'白色玩具车',21:'奶油色泰迪熊',22:'黑灰色玩偶'}
    for layout in (20,21,22):
        x,y=lookup[25,layout],lookup[250,layout]
        lines.append(f"| L{layout} / {targets[layout]} | {x['score']:g} | {y['score']:g} | "
                     f"{x['simulation_seconds']:.2f} → {y['simulation_seconds']:.2f} | "
                     f"{x['model_calls']} → {y['model_calls']} | {x['invalid_actions']} → {y['invalid_actions']} | "
                     f"{x['protection_interruptions']} → {y['protection_interruptions']} |")
    lines+=['','两组各仅三局、每个 layout 一局；这些是观测到的通过率，不能据此估计广泛任务上的稳定成功率。','',
        '## 机械臂移动与抓取','',
        'L20/L22 均使用右臂，L21 均使用左臂。六局的非工作臂腕部位置在已保存的 40 ms 观测中保持不变；'
        '工作臂数值有限、未超过日志提供的关节位置限制；没有运动前拒绝或运动学中断。'
        '图片检查显示 L21/L22 完成接近、闭合、抬起，未见明显瞬移或错误手臂动作。'
        '这些判断来自日志与离散画面，不是逐物理步的碰撞/力验收。','',
        'L20 两组均出现物体相对夹爪转动和多次重新开闭。两组都有 3 次进入闭合命令区间（gripper≤0.05），'
        '因此最终成功并不代表一次抓稳。两组最终抓姿也不同。', '',
        '保护事件均由闭合命令保持期间的指节继续变化超过 3 mm 触发，事件时腕部误差仅约 0.13..1.08 mm，'
        '并非腕部 35 mm 跟踪阈值触发。指节变化方向均为继续闭合，不能单凭该阈值将全部事件判作滑移；'
        'L20 事件画面同时显示玩具车相对夹爪发生转动。','',
        '| 40 ms 共同采样 / 工作臂 | 腕部误差 P95 mm 25→250 | 腕部误差最大 mm 25→250 | 路径长度 m 25→250 | 加速度 RMS m/s² 25→250 | jerk RMS m/s³ 25→250 |',
        '|---|---:|---:|---:|---:|---:|']
    for layout in (20,21,22):
        x,y=[lookup[hz,layout]['arms'][lookup[hz,layout]['active_arm']] for hz in (25,250)]
        lines.append(f"| L{layout} | {x['wrist_error_common_40ms_mm']['p95']:.2f} → {y['wrist_error_common_40ms_mm']['p95']:.2f} | "
            f"{x['wrist_error_common_40ms_mm']['maximum']:.2f} → {y['wrist_error_common_40ms_mm']['maximum']:.2f} | "
            f"{x['path_length_m']:.3f} → {y['path_length_m']:.3f} | "
            f"{x['acceleration_m_s2']['rms']:.2f} → {y['acceleration_m_s2']['rms']:.2f} | "
            f"{x['jerk_m_s3']['rms']:.1f} → {y['jerk_m_s3']['rms']:.1f} |")
    lines+=['','jerk 为位置三阶差分的模长 RMS，用于本次共同 40 ms 网格的描述，受采样、接触和重规划影响。'
        '250 Hz 的平缓程度指标较低，但三局均用了更长的仿真时间，不能将其归因于频率本身。'
        'L20 的 25 Hz 峰值跟踪残差约 33 mm，接近 35 mm 保护线；250 Hz 的共同采样峰值约 13.69 mm。'
        'P95 在 L21 改善，在 L20/L22 没有改善；不存在所有跟踪指标一致提升的结果。','',
        '250 Hz 在原始 4 ms 样本下的腕部最大残差为 L20 14.97 mm、L21 2.87 mm、L22 6.85 mm；'
        '共同采样表用于频率间对照，原始最大值用于发现采样间瞬态。','',
        '## 效率与输入体积','',
        '| 指标（三局合计） | 25 Hz | 250 Hz |', '|---|---:|---:|',
        f"| 成功 | {a['successes']}/3 | {b['successes']}/3 |",
        f"| 仿真秒 | {a['simulation_seconds']:.2f} | {b['simulation_seconds']:.2f} |",
        f"| 模型调用 | {a['model_calls']} | {b['model_calls']} |",
        f"| 输入 token | {a['input_tokens']:,} | {b['input_tokens']:,} |",
        f"| 输出 token | {a['output_tokens']:,} | {b['output_tokens']:,} |",
        f"| 模型请求墙钟秒 | {a['model_request_seconds']:.1f} | {b['model_request_seconds']:.1f} |",
        f"| 总墙钟秒（含启动/退出） | {a['wall_seconds']:.1f} | {b['wall_seconds']:.1f} |",
        f"| API 重试 | {a['api_retry_attempts']} | {b['api_retry_attempts']} |",
        f"| 保护中断 / 模型决策 | {a['protection_interruptions']}/{a['model_calls']} ({100*a['protection_interruption_rate']:.2f}%) | "
        f"{b['protection_interruptions']}/{b['model_calls']} ({100*b['protection_interruption_rate']:.2f}%) |",'',
        f"250 Hz 仿真完成时间合计增加 {(b['simulation_seconds']/a['simulation_seconds']-1)*100:.1f}%，"
        f"模型调用减少 {(1-b['model_calls']/a['model_calls'])*100:.1f}%，但输入 token 增加到 "
        f"{b['input_tokens']/a['input_tokens']:.2f} 倍。输入 token 为服务端 usage 报告值，不是人民币/API 账单。",'',
        f"recent_executed_segments 占本轮 question JSON 字节比例：25 Hz 为 "
        f"{100*a['current_recent_segments_bytes']/a['current_question_bytes']:.1f}%，250 Hz 为 "
        f"{100*b['current_recent_segments_bytes']/b['current_question_bytes']:.1f}%。"
        '其中保留每周期、每臂的 tracking 明细；最近八段以及上一轮消息会重复携带大量历史明细。'
        '此前的相等字段去重没有消除这些 recent 段与历史消息之间的重叠。此处字节占比是该字段的 JSON 序列化估计，'
        '不等同于 token 归因；总 token 增加则由实际 usage 直接确认。','',
        '## 指标口径与后续评价','',
        '- invalid_actions：当前策略计数只覆盖此 joint 分支的 rejected_before_motion；应同时报告运动学中断、保护中断、终止截断。',
        '- 跟踪：同时保留原始控制周期峰值和固定 40 ms 比较值，分别看瞬态和频率间差异。',
        '- 平稳性：看固定时间网格的速度、加速度、jerk；结合完成时长、路径长度，防止把更慢误认为控制更好。',
        '- 抓取：记录闭合循环、重抓/物体相对运动；成功判据之外可增加姿态保持、稳定保持时长。当前未提供物体连续 6D 轨迹，不能量化统一的姿态/滑移率。',
        '- 资源：模型调用、输入/输出 token、模型等待、控制执行墙钟应分别统计。',
        '- 补充测量：连续接触力、关节力矩、碰撞/自碰撞、能量、任务目标连续高度和抓取后保持时长，现有日志不足以完整评价。',
        '- 若要隔离频率作用，应固定相同关节目标、物理时长、夹爪时序并复放，再做多 seed 重复。目前两组为独立模型决策，不能作单因素因果比较。','',
        '25 Hz 的 steps≤20（最多 0.8 秒）与 250 Hz 的 steps≤2500（最多 10 秒，受剩余回合时间截断）不同；'
        'settle≤2 分别为最多 80 ms / 8 ms；保护检查频率不同。两组的 0.15 rad/步增量阈值还对应不同的时间尺度。'
        '物理采样本来均是 250 Hz，故本次不是将整个仿真从 25 Hz 提升到 250 Hz。','',
        '## 查看证据','',
        '- [交互页面](index.html)：选择 layout 播放按相同仿真时间拼接的视频。',
        '- [指标总览](overview.png) / [移动与夹爪曲线](motion.png)，另提供对应 SVG。',
        '- [L20 抓取事件图](L20-grasp-events.jpg)：两行按事件对应，图中时刻并不相同。',
        '- [L20 对比视频](L20-comparison.mp4) / [L21 对比视频](L21-comparison.mp4) / [L22 对比视频](L22-comparison.mp4)。',
        '- [原始统计 JSON](metrics.json) / [配对指标 CSV](paired-metrics.csv) / [来源 SHA-256](source-sha256.json)。',
        '- 视频为原始 RGB 图像按 40 ms 网格重排：左侧 25 Hz、右侧 250 Hz，已成功的一侧停留在终止帧。'
        '模型等待墙钟不在视频中；不是再运行的仿真。','',
        '复现：`/data/lanruya/robodojo/envs/RoboDojo-private/bin/python -B scripts/analyze_control_frequencies.py '
        '--output <新的分析目录> --videos`。','']
    (output/'REPORT.md').write_text('\n'.join(lines))
    import html
    historical_rows=''.join('<tr>'+''.join(f'<td>{html.escape(str(value))}</td>' for value in (
        r['task'],f"{int(r['success'])}/1",'未测',r['score'],r['invalid_actions'],
        r['execution_statuses'].get('kinematic_rejection',0),r['execution_statuses'].get('tracking_or_gripper_change',0)))+'</tr>'
        for r in historical)
    paired_rows=''.join('<tr>'+''.join(f'<td>{html.escape(str(value))}</td>' for value in (
        f'L{layout}',lookup[25,layout]['simulation_seconds'],lookup[250,layout]['simulation_seconds'],
        lookup[25,layout]['model_calls'],lookup[250,layout]['model_calls']))+'</tr>' for layout in (20,21,22))
    (output/'index.html').write_text('''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>25Hz / 250Hz 控制对比</title>
<style>body{max-width:1180px;margin:32px auto;padding:0 20px;background:#f5f7fa;color:#17212f;font:16px/1.65 system-ui}
h1{font-size:29px}section{background:white;padding:22px;margin:22px 0;border-radius:12px}table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:9px;border-bottom:1px solid #ddd;text-align:left}img,video{width:100%;height:auto}a{color:#2463af}select{padding:8px;font-size:16px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}.card{padding:16px;background:#eaf0f7;border-radius:8px}
.card strong{display:block;font-size:23px}</style>
<h1>25Hz 与 250Hz：控制结果与移动质量</h1><p>general_pickup，L20/L21/L22 各一局。顺序均为 25Hz → 250Hz。两组均成功；没有成功率提升证据。</p>
<div class="cards"><div class="card">成功<strong>3/3 → 3/3</strong></div><div class="card">仿真总秒<strong>10.64 → 13.16</strong></div>
<div class="card">模型调用<strong>43 → 39</strong></div><div class="card">输入 token<strong>1.18M → 5.09M</strong></div>
<div class="card">原无效动作<strong>0 → 0</strong></div><div class="card">保护中断<strong>2 → 3</strong></div></div>
<section><h2>配对结果</h2><table><tr><th>Layout</th><th>25Hz 仿真秒</th><th>250Hz 仿真秒</th><th>25Hz 调用</th><th>250Hz 调用</th></tr>'''
        +paired_rows+'''</table><p>250Hz 的加速度/jerk 较低，但三局均完成得更慢；跟踪误差也未在每项指标上改善。</p>
<img src="overview.png" alt="完成时长、调用、误差、路径与输入 token"></section>
<section><h2>按相同仿真时刻看动作</h2><label>Layout <select id="layout"><option value="20">L20 白色玩具车</option>
<option value="21">L21 泰迪熊</option><option value="22">L22 黑灰色玩偶</option></select></label>
<p>左 25Hz，右 250Hz；成功的一侧停留在终止帧，视频不含模型等待时间。</p><video id="video" controls preload="metadata" src="L20-comparison.mp4"></video>
<p><a id="contact" href="L20-contact-sheet.jpg">查看该局关键帧</a> · <a href="L20-grasp-events.jpg">L20 夹爪保护前后</a></p></section>
<section><h2>机械臂与夹爪</h2><p>L20/L22 为右臂，L21 为左臂；非工作臂位置保持不变。L20 两组都发生多次开闭和物体相对转动，最终均恢复并成功。
原 invalid_actions 不含夹爪保护中断，不能将 0 解读成全程没有中断。</p><img src="motion.png" alt="腕部高度、速度、跟踪残差、手指位置"></section>
<section><h2>其他任务的数据覆盖</h2><p>以下为较早的 25Hz control-only L20，I/O 版本不同，250Hz 未运行。12 次运动学中断均为执行部分动作后中断，原 invalid_actions 未包含。</p>
<div style="overflow:auto"><table><tr><th>任务</th><th>25Hz 成功</th><th>250Hz</th><th>分数</th><th>原无效</th><th>运动学中断</th><th>保护中断</th></tr>'''
        +historical_rows+'''</table></div></section><p><a href="REPORT.md">完整分析与口径</a> · <a href="metrics.json">JSON</a> · <a href="paired-metrics.csv">CSV</a></p>
<script>document.getElementById('layout').addEventListener('change',function(){const l=this.value;const v=document.getElementById('video');v.src='L'+l+'-comparison.mp4';v.load();document.getElementById('contact').href='L'+l+'-contact-sheet.jpg';});</script></html>''')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--videos',action='store_true')
    args=parser.parse_args()
    data,frame_sets=analyze(args.output.resolve())
    data['visuals']=visualizations(args.output.resolve(),data,frame_sets,args.videos)
    save(args.output.resolve()/'metrics.json',data)
    write_report(args.output.resolve(),data)
    print(json.dumps(data['aggregate'],indent=2))
