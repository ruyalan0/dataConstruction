"""Maintain a paired report and archive/notify once both evaluation cohorts end."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time
from urllib.request import Request, urlopen

from prepare_eight_eval import TASKS, save
from report_eight_eval import end_reason
from run_eight_eval import hdfs_archive, now


def case_record(folder):
    if not folder.exists(): return {'phase': 'not_selected'}
    if (folder/'result.json').exists():
        record = json.loads((folder/'result.json').read_text())
        record.update(phase='finished', end_reason=end_reason(folder, record))
        stop = folder/'administrative-stop.json'
        if stop.exists() and record.get('policy_exit') == -15:
            record.update(end_reason='cancelled_already_full_score',
                          administrative_stop=json.loads(stop.read_text()))
        durability = folder/'durability.json'
        record['final_archive_verified'] = durability.exists() and json.loads(durability.read_text())['final']
        return record
    if (folder/'status.json').exists():
        return dict(json.loads((folder/'status.json').read_text()), phase='running')
    return {'phase': 'starting'}


def cell(record):
    if record['phase'] == 'not_selected': return '—'
    if record['phase'] in ('starting', 'queued'): return record['phase']
    if record['phase'] == 'running': return f"step {record['step']}, calls {record['model_calls']}"
    if record['end_reason'] == 'cancelled_already_full_score': return 'cancelled: baseline already 100'
    native = record.get('native')
    if not native: return record['end_reason']+'; no native verdict'
    prefix = '' if native['native_terminal'] else ' prefix'
    return f"{native['score']:g}{prefix}; {record['end_reason']}"


def summarize(root, revision):
    baseline = root/'artifacts/eight-task-eval'
    iteration = root/'artifacts/skill-iterations'/revision
    plan = json.loads((iteration/'plan.json').read_text())
    selected = {(task, layout) for task, layout, _ in plan['cases']}
    scope_path = iteration/'scope-amendment.json'
    scope = json.loads(scope_path.read_text()) if scope_path.exists() else None
    retained = {(case['task'], case['layout']) for case in scope['retained']} if scope else selected
    rows = []
    for task in TASKS:
        for layout in (20, 21):
            record = case_record(iteration/'cases'/f"{task}-L{layout}-attempt{plan['attempt']}")
            if record['phase'] == 'not_selected' and (task, layout) in selected: record['phase'] = 'queued'
            rows.append({'task': task, 'layout': layout, 'in_scope': (task, layout) in retained,
                'baseline': case_record(baseline/'cases'/f'{task}-L{layout}-attempt1'),
                'iteration': record})
    counts = {cohort: dict(Counter(row[cohort].get('end_reason', row[cohort]['phase']) for row in rows))
              for cohort in ('baseline', 'iteration')}
    report = {'at': now(), 'revision': revision, 'counts': counts, 'rows': rows,
              'scope': scope, 'scope_cases': len(retained),
              'scope_counts': dict(Counter(row['iteration'].get('end_reason', row['iteration']['phase'])
                                          for row in rows if row['in_scope'])),
              'skill_manifest': json.loads((iteration/'skills-manifest.json').read_text())}
    save(iteration/'comparison.json', report)
    lines = ['# Skill iteration comparison', '', f"Updated: {report['at']}", '',
             f"Baseline Harness: 5e17233. Iteration Harness: {plan['harness_commit'][:7]}. Skill revision: {revision} ({report['skill_manifest']['skill_commit'][:7]}).",
             'Joint-only; all trials start from native initial states. Baseline and adaptive follow-ups remain separate.', '',
             '| Task | Layout | Baseline | Skill iteration |', '|---|---|---|---|']
    for row in rows:
        lines.append(f"| {row['task']} | L{row['layout']} | {cell(row['baseline'])} | {cell(row['iteration'])} |")
    lines.extend(['', 'Prefix scores describe incomplete episodes, including Harness interruptions and explicit model stops.',
                  'Only native terminal verdicts establish full-episode outcomes. The selected follow-ups do not estimate a general success rate.'])
    if scope:
        lines.extend(['', f"Scope amended: retain {len(retained)} cases; {len(scope['excluded'])} baseline-full-score repetitions excluded.",
                      'Administrative cancellations are not model failures. Original plans and raw results remain preserved.',
                      'Retained pending-baseline cases are labeled in scope-amendment.json; pending does not mean failed.'])
    (iteration/'comparison.md').write_text('\n'.join(lines)+'\n')
    subprocess.run([sys.executable, str(root/'scripts/report_eight_eval.py'), '--campaign', str(baseline)],
                   check=True, stdout=subprocess.DEVNULL)
    return report


def supervise(folder):
    if (folder/'completed.json').exists(): return
    pid = json.loads((folder/'campaign.json').read_text())['pid']
    os.kill(pid, 0)  # Fail immediately if a supervisor disappears without completion.


def verify_loaded_skills(iteration, plan):
    manifest = json.loads((iteration/'skills-manifest.json').read_text())
    verified = []
    for task, layout, _ in plan['cases']:
        folder = iteration/'cases'/f"{task}-L{layout}-attempt{plan['attempt']}"
        status = folder/'status.json'
        if not status.exists(): continue
        worker_case = Path(json.loads(status.read_text())['worker_case'])
        path = worker_case/'policy/skills.json'
        if not path.exists(): continue
        actual = json.loads(path.read_text())
        config = json.loads((worker_case/'config.json').read_text())
        expected = {str(iteration/'skills'/name/'SKILL.md'): manifest['files'][name+'/SKILL.md']
                    for name in config['skill_names']}
        assert actual == expected, 'Loaded skill mismatch: '+folder.name
        verified.append({'case': folder.name, 'skill_hashes': actual})
    save(iteration/'loaded-skills-verification.json', {'at': now(), 'expected_cases': len(plan['cases']),
                                                     'verified_cases': len(verified), 'cases': verified})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    baseline = args.root/'artifacts/eight-task-eval'
    iteration = args.root/'artifacts/skill-iterations'/args.revision
    plan = json.loads((iteration/'plan.json').read_text())
    if args.once:
        print(json.dumps(summarize(args.root, args.revision)['counts'])); return
    assert os.environ.get('ARNOLD_WORKER_ID') == '0000000'
    assert not (iteration/'completion-notification.json').exists(), 'Completion already notified'
    webhook = os.environ['EVAL_COMPLETION_WEBHOOK']
    while True:
        verify_loaded_skills(iteration, plan)
        report = summarize(args.root, args.revision)
        if all((folder/'completed.json').exists() for folder in (baseline, iteration)): break
        for folder in (baseline, iteration): supervise(folder)
        time.sleep(30)
    sys.path.insert(0, str(args.root/'artifacts/baseline-5e17233'))
    from egg_harness.freeze import verify
    verify(args.root/'artifacts/baseline-5e17233')
    runtime = args.root/'artifacts/baseline-5e17233' if plan['harness_commit'].startswith('5e17233') else Path(plan['baseline'])
    assert verify(runtime)['commit'] == plan['harness_commit']
    manifest = report['skill_manifest']
    assert all(hashlib.sha256((iteration/'skills'/name).read_bytes()).hexdigest() == value
               for name, value in manifest['files'].items())
    completed = [json.loads((folder/'completed.json').read_text()) for folder in (baseline, iteration)]
    errors = [result for cohort in completed for result in cohort['results']
              if result['status'] == 'orchestration_or_durability_error']
    save(iteration/'completion-check.json', {'at': now(), 'harness_and_skills_verified': True,
        'orchestration_or_durability_errors': errors,
        'all_case_archives_final': all(row[cohort].get('final_archive_verified', False)
            for row in report['rows'] for cohort in ('baseline', 'iteration')
            if row[cohort]['phase'] != 'not_selected')})
    archive = Path('/tmp/astra-eight-tasks-5e17233/skill-iterations')/args.revision/'final-report.tar.gz'
    with tarfile.open(archive, 'w:gz') as target:
        for cohort, folder in (('baseline', baseline), ('iteration', iteration)):
            for name in ['campaign.json', 'completed.json', 'results.json', 'summary.md', 'summary.json',
                         'comparison.md', 'comparison.json', 'completion-check.json', 'skills-manifest.json',
                         'loaded-skills-verification.json', 'analysis', 'source-inputs-receipt.json']:
                if (folder/name).exists(): target.add(folder/name, arcname=cohort+'/'+name)
            for path in (folder/'cases').glob('*/durability.json'):
                target.add(path, arcname=cohort+'/'+str(path.relative_to(folder)))
            for path in [folder/'scope-amendment.json', *(folder/'cases').glob('*/administrative-stop.json')]:
                if path.exists(): target.add(path, arcname=cohort+'/'+str(path.relative_to(folder)))
        target.add(args.root/'EIGHT-TASK-EVAL-PROGRESS.md', arcname='EIGHT-TASK-EVAL-PROGRESS.md')
        if (args.root/'TASK-SKILL-ITERATION.md').exists():
            target.add(args.root/'TASK-SKILL-ITERATION.md', arcname='TASK-SKILL-ITERATION.md')
        target.add(Path(__file__), arcname='orchestration/watch_skill_eval.py')
    hdfs = json.loads((iteration/'campaign.json').read_text())['hdfs_root']
    hdfs_archive(archive, hdfs+'/final-report.tar.gz', iteration/'final-report-receipt.json')
    counts = report['counts']
    message = (f"【General-Pickup】8任务 L20/L21 基线16场及技能{args.revision}范围内复测{report['scope_cases']}场均已结束。"
               '本轮Harness提交'+plan['harness_commit'][:7]+'，skill提交'+manifest['skill_commit'][:7]+'。'
               f"原生成功：基线{counts['baseline'].get('native_success', 0)}/16，范围内复测{report['scope_counts'].get('native_success', 0)}/{report['scope_cases']}。"
               f"因基线已满分而取消{counts['iteration'].get('cancelled_already_full_score', 0)}场，不计入模型失败。"
               '其余结果包含原生失败、主动停止或中断，详见分项报告；结果不代表总体成功率。'
               f'归档/编排错误{len(errors)}；最终报告已校验归档HDFS。报告：'+str(iteration/'comparison.md'))
    request = Request(webhook, data=json.dumps({'msg_type': 'text', 'content': {'text': message}}).encode(),
                      headers={'Content-Type': 'application/json'}, method='POST')
    with urlopen(request, timeout=30) as response: result = json.load(response)
    save(iteration/'completion-notification.json', {'at': now(), 'response': result, 'message': message})
    assert result.get('code', result.get('StatusCode')) == 0, result


if __name__ == '__main__': main()
