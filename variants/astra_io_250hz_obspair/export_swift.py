"""Export actual observation-pair runs; each Swift row has the same one-user input."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys

from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE / 'harness'), str(ROOT / 'scripts')]
from egg_harness.control_timing import validate_timing_record
from egg_harness.observation_pair import CAMERAS, PROTOCOL
from export_io_variant_ms_swift import NATIVE_PREFIX, TEXT_PREFIX, compact, call_from, digest, require
from export_timed_io_ms_swift import validate_arguments


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def records_for(run):
    result=read(run/'results.json')[0]
    launch=read(run/'launch-record.json')
    native=result['official_observation']
    require(result['status']=='native_end' and native['native_terminal'] and native['valid_sample']
            and not native['unstable'], 'Only valid native terminal episodes can be exported')
    require(result['policy_returncode']==result['simulator_returncode']==0 and result['release_unchanged'],
            'Runtime failed or source changed')
    require(launch['io_variant']=='astra_io_250hz_obspair' and launch['input_protocol']==PROTOCOL
            and launch['model_history_replay'] is False, 'Use an actual observation-pair run')
    layout=result['seed_index'];model=run/f'policy-{layout}/model'
    timing=read(run/f'seed-{layout}/control-timing.json');validate_timing_record(timing)
    paths=sorted(model.glob('*.request.json'))
    require(len(paths)==result['policy']['model_calls'] and bool(paths), 'Decision count mismatch')
    tools=read(model/'native-tools.json')['tools']
    require([tool['name'] for tool in tools]==['control_arms'], 'Unexpected tools')
    output=[];previous=None;end=0
    for path in paths:
        stem=path.name.removesuffix('.request.json');request=read(path)
        value=json.loads(request['question']);observation=value['observation'];history=value['history']
        require(request['input_protocol']==value['input_protocol']==PROTOCOL and not request['model_history_replay']
                and request['replayed_call_id'] is None and request['step']==observation['step']==end,
                'Unexpected replay or observation boundary')
        call=call_from(model/f'{stem}.response.json');validate_arguments(call['arguments'])
        feedback=read(model/f'{stem}.tool-result.json');actual=json.loads(feedback['output'])
        require(feedback['call_id']==call['call_id'], 'Feedback call mismatch')
        args=json.loads(call['arguments']);executed=actual['feedback']['executed_steps']
        require(actual['step']==end+executed and 0<=executed<=args['steps']+args['settle'], 'Execution boundary mismatch')
        expected_names=(['previous_'+name for name in CAMERAS] if previous else [])+['current_'+name for name in CAMERAS]
        require([image['name'] for image in request['images']]==expected_names, 'Image order mismatch')
        if previous:
            prior=json.loads(previous['request']['question'])['observation']
            snapshot={key:deepcopy(prior[key]) for key in ('step','state','arm_posture') if key in prior}
            require(history['available'] and history['previous_observation']==snapshot
                    and history['duration_ms']==(observation['step']-prior['step'])*4, 'Previous decision observation mismatch')
            require(observation['memory']==json.loads(previous['call']['arguments'])['memory'], 'Memory was not propagated')
            require([image['sha256'] for image in request['images'][:3]]==[
                image['sha256'] for image in previous['request']['images'][-3:]], 'Previous images mismatch')
        else:
            require(history==dict(available=False,duration_ms=None,previous_observation=None), 'First request has history')
        content=[dict(type='input_text',text=request['question'])]
        for image in request['images']:
            require(digest(Path(image['path']))==image['sha256'], 'Source image changed')
            content.extend([dict(type='input_text',text=image['name']),
                            dict(type='input_image',detail='high',image_sha256=image['sha256'])])
        require(read(model/f'{stem}.api-input.readable.json')['input']==[dict(role='user',content=content)],
                'Actual API input differs from observation pair')
        row=dict(run=run,layout=layout,stem=stem,request=request,call=call,feedback=feedback,
                 end_step=actual['step'],tool=tools[0])
        output.append(row);previous=row;end=actual['step']
    require(end==result['action_count']==native['steps'] and actual['ended'], 'Final native result mismatch')
    return output


def sample_for(output,record,copy_images=False):
    request=record['request'];system=request['system']
    require(system.count(NATIVE_PREFIX)==1,'Unknown system prompt')
    system=system.replace(NATIVE_PREFIX,TEXT_PREFIX,1)+'\nOutput only the control_arms argument JSON; no Markdown. '
    system+='Keep observation.memory and compare the previous/current observations. No tool history is serialized.\n'
    system+='Output schema:\n'+compact(record['tool'])
    text=request['question'];images=[]
    for index,image in enumerate(request['images']):
        target=output/'images'/f"L{record['layout']}"/f"{record['stem']}-{image['name']}.jpg"
        if copy_images:
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(image['path'],target)
        text+='\n'+image['name']+': <image>'
        images.append(str(target))
    return dict(messages=[dict(role='system',content=system),dict(role='user',content=text),
                          dict(role='assistant',content=record['call']['arguments'])],images=images)


def export(runs,output):
    records=[record for run in runs for record in records_for(run)]
    require(len({record['layout'] for record in records})==len(runs),'Use distinct layouts')
    output.mkdir(parents=True,exist_ok=False)
    samples=[sample_for(output,record,True) for record in records]
    content=''.join(compact(row)+'\n' for row in samples)
    (output/'train.jsonl').write_text(content);(output/'all.jsonl').write_text(content)
    index=[dict(line=i,layout=record['layout'],decision=int(record['stem']),source_run=str(record['run']),
                observation_step=record['request']['step'],end_step=record['end_step']) for i,record in enumerate(records,1)]
    (output/'index.jsonl').write_text(''.join(compact(row)+'\n' for row in index))
    save(output/'example-first.json',samples[0])
    if len(samples)>1:save(output/'example-followup.json',samples[1])
    save(output/'manifest.json',dict(source_runs=list(map(str,runs)),samples=len(samples),input_protocol=PROTOCOL,
         memory_location='observation.memory',action_hz=250,observation_hz=25,model_history_replay=False,
         format='ms-swift messages + images JSONL',labels='exact effective function_call.arguments',
         source_sha256={str(path):digest(path) for run in runs for path in sorted(run.rglob('*.json'))
                        if path.is_file() and 'runtime-assets' not in path.parts
                        and not path.is_relative_to(output) and 'swift-dataset' not in path.parts}))
    (output/'README.md').write_text('# Observation-pair Swift data\n\n'
        f'{len(samples)} samples from actual completed observation-pair runs. Each row has system/user/assistant '
        'messages and ordered images. The user message contains two measured observations and observation.memory; '
        'no previous tool cycle. Labels preserve exact effective arguments. steps/settle use 4 ms control ticks.\n\n'
        'Image paths are absolute; update them when moving the dataset. train.jsonl and all.jsonl are identical.\n\n'
        f'Use --dataset {output/"train.jsonl"} --split_dataset_ratio 0.\n')
    return validate(output)


def validate(output):
    manifest=read(output/'manifest.json');records=[record for run in manifest['source_runs'] for record in records_for(Path(run))]
    require(all(Path(path).is_file() and digest(Path(path))==sha for path,sha in manifest['source_sha256'].items()),
            'Source files changed since export')
    samples=[json.loads(line) for line in (output/'train.jsonl').read_text().splitlines()]
    require(len(samples)==len(records)==manifest['samples'], 'Sample count mismatch')
    require((output/'train.jsonl').read_bytes()==(output/'all.jsonl').read_bytes(),'train/all mismatch')
    for sample,record in zip(samples,records):
        require(sample==sample_for(output,record),'Source input or label mismatch')
        require(sample['messages'][1]['content'].count('<image>')==len(sample['images']),'Placeholder mismatch')
        for image,source in zip(sample['images'],record['request']['images']):
            require(digest(Path(image))==source['sha256'],'Exported image changed')
            with Image.open(image) as picture:picture.load()
    report=dict(passed=True,validated_at=datetime.now(timezone.utc).isoformat(),samples=len(samples),
                exact_source_inputs_and_labels=True,memory_in_observation=True,tool_history_replayed=False,
                dataset_sha256=digest(output/'train.jsonl'),model_template_tested=False)
    save(output/'validation.json',report);return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,action='append')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--validate-only',action='store_true')
    args=parser.parse_args()
    if not args.validate_only and not args.run:parser.error('--run is required')
    result=validate(args.output.resolve()) if args.validate_only else export([run.resolve() for run in args.run],args.output.resolve())
    print(json.dumps(result,indent=2))
