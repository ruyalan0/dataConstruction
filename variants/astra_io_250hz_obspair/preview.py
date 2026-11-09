"""Preview new inputs from saved measurements without requesting a model or a simulator."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(HERE / 'harness')]
from egg_harness.native_responses import TOOL_INSTRUCTIONS
from egg_harness.observation_pair import CAMERAS, build_pair
from policy import JOINT_SYSTEM


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def audit_reference(path):
    counts, cameras, tasks, durations = Counter(), Counter(), Counter(), []
    for line in path.open():
        if not line.strip():
            continue
        row = json.loads(line)
        assert set(row) == {'messages', 'images'}
        assert [item['role'] for item in row['messages']] == ['system', 'user', 'assistant']
        user = row['messages'][1]['content']
        history = json.loads(re.search(r'^History: (.*)$', user, re.M).group(1))
        assert user.count('<image>') == len(row['images']) == (6 if history['available'] else 3)
        assert set(json.loads(row['messages'][-1]['content'])) == {'arms', 'steps'}
        assert (history['previous_state'] is not None) == history['available']
        if history['available']:
            assert history['duration_ms'] >= 0
            durations.append(history['duration_ms'])
        counts['rows'] += 1
        counts['history_available' if history['available'] else 'first_decision'] += 1
        cameras[len(row['images'])] += 1
        tasks[user.splitlines()[0]] += 1
        counts['memory_present'] += 'memory' in user
    return dict(passed=True, source=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                counts=dict(counts), image_count_distribution=dict(cameras), tasks=dict(tasks),
                duration_ms_min=min(durations), duration_ms_max=max(durations),
                image_bytes_verified=False,
                scope='JSON structure and placeholder order checked; referenced remote image paths are not local')


def preview(runs, output):
    output.mkdir(parents=True, exist_ok=False)
    skills='\n'.join((HERE / 'harness/skills' / name / 'SKILL.md').read_text() for name in
                     read(HERE / 'general_pickup.json')['skill_names'])
    instructions=TOOL_INSTRUCTIONS+'\n'+JOINT_SYSTEM+'\n'+skills
    rows, sources = [], set()
    old_bytes=new_bytes=0
    for run in runs:
        result=read(run/'results.json')[0]
        layout=result['seed_index']
        previous=None
        last_memory=None
        folder=output/f'L{layout}'
        folder.mkdir()
        paths=sorted((run/f'policy-{layout}/model').glob('*.request.json'))
        for path in paths:
            stem=path.name.removesuffix('.request.json')
            request=read(path)
            source=json.loads(request['question'])
            source['memory']=source['memory'] if last_memory is None else last_memory
            images={item['name']:item['path'] for item in request['images']}
            pair,ordered,current=build_pair(source,images,previous)
            content=[dict(type='input_text',text=json.dumps(pair,ensure_ascii=False,allow_nan=False))]
            manifest=[]
            for name,image in ordered.items():
                image=Path(image)
                digest=hashlib.sha256(image.read_bytes()).hexdigest()
                sources.add(image)
                content.extend([dict(type='input_text',text=name),dict(type='input_image',image_sha256=digest,detail='high')])
                manifest.append(dict(name=name,path=str(image),sha256=digest))
            response_path=path.with_name(stem+'.response.json')
            response=read(response_path)
            call=next(item for item in response['output'] if item['type']=='function_call')
            actual_input=read(path.with_name(stem+'.api-input.readable.json'))['input']
            old_text=sum(len(part['text'].encode()) for item in actual_input for part in item.get('content',[])
                         if part['type']=='input_text')
            old_text+=sum(len(item.get('arguments','').encode())+len(item.get('output','').encode()) for item in actual_input)
            new_text=sum(len(part['text'].encode()) for part in content if part['type']=='input_text')
            old_bytes+=old_text;new_bytes+=new_text
            assert pair['observation']['state']==source['state']
            assert pair['observation']['memory']==source['memory']
            assert pair['history']['previous_observation'] is None or pair['history']['previous_observation']['step']==previous['step']
            assert len(ordered)==(6 if previous else 3)
            preview_record=dict(scope='Offline input-format conversion only; no new model or robot execution',
                instructions=instructions,input=[dict(role='user',content=content)],images=manifest,
                reference_response=str(response_path),reference_action_arguments=call['arguments'])
            save(folder/f'{stem}.context.json',pair)
            save(folder/f'{stem}.input.json',preview_record)
            rows.append(dict(layout=layout,decision=int(stem),source_request=str(path),source_response=str(response_path),
                             context=str(folder/f'{stem}.context.json'),input=str(folder/f'{stem}.input.json'),
                             images=len(ordered),old_nonimage_user_call_feedback_bytes=old_text,new_user_text_bytes=new_text))
            sources.update((path,response_path,path.with_name(stem+'.api-input.readable.json')))
            previous=current
            last_memory=json.loads(call['arguments'])['memory']
    report=dict(passed=True,created_at=datetime.now(timezone.utc).isoformat(),samples=len(rows),
                per_layout=dict(Counter(row['layout'] for row in rows)),image_references=sum(row['images'] for row in rows),
                memory_location='observation.memory',complete_tool_cycles_replayed=0,
                exact_current_states=True,exact_previous_decision_states=True,all_three_previous_cameras=True,
                nonimage_input_bytes=dict(old_user_plus_call_feedback=old_bytes,new_user_only=new_bytes,
                    reduction_fraction=1-new_bytes/old_bytes,
                    scope='Excludes system/tools, image bytes and encrypted reasoning; not a token or performance measurement'),
                scope='Offline preview of historical measurements and labels, not new-policy success evidence or new training trajectories')
    save(output/'manifest.json',dict(report,records=rows))
    save(output/'source-sha256.json',{str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(sources)})
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,action='append',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(preview([path.resolve() for path in args.run],args.output.resolve()),ensure_ascii=False,indent=2))
