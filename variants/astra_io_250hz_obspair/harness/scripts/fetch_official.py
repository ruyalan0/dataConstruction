"""Archive complete public task tables with source hashes."""
import concurrent.futures
import hashlib
import html
import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
TASKS = 'align_blocks classify_objects_by_language solve_equation general_pickup stack_blocks arrange_largest_number classify_objects play_tic_tac_toe insert_key deposit_coin hang_mugs press_by_number stack_blocks_by_language swap_blocks cover_blocks sort_nesting_dolls_by_size imitate_sorting_sequence match_and_pick_from_conveyor put_bottles_into_dustbin fill_egg_holder'.split()
def fetch(task):
    url = 'https://robodojo-benchmark.com/doc/sim-tasks/' + task.replace('_', '-') + '/'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=40) as response:
        data = response.read()
    tables = re.findall(r'<table .*?</table>', data.decode(), flags=re.S)
    rows = []
    for table in tables:
        for row in re.findall(r'<tr .*?</tr>', table, flags=re.S):
            cells = re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', row, flags=re.S)
            rows.append([' '.join(html.unescape(re.sub('<[^>]+>', ' ', c)).split()) for c in cells])
    if not any(r[0] == 'Instruction' for r in rows if r):
        raise ValueError(f'No official instruction: {task}')
    folder = Path('sources/official')
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (task + '.html')).write_bytes(data)
    record = {'task': task, 'url': url, 'fetched_at': datetime.now(timezone.utc).isoformat(), 'sha256': hashlib.sha256(data).hexdigest(), 'rows': rows}
    (folder / (task + '.json')).write_text(json.dumps(record, indent=2))
    return record
if __name__ == '__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
        records = list(pool.map(fetch, TASKS))
    Path('configs/public-tasks.json').write_text(json.dumps({r['task']: r for r in records}, indent=2))
    print(f'Archived {len(records)} complete official task tables')
