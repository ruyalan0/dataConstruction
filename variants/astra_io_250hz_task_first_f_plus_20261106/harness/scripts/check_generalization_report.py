"""Actual API failures must never become terminal evaluation scores."""
from pathlib import Path
from report_generalization import report

failed = report(Path('artifacts/classify-language-frozen24-v1'))
assert failed['mean_score'] is None and failed['successes'] == 0
assert failed['episodes'][0]['nonterminal_snapshot_score'] == 10
assert failed['episodes'][0]['model_usage']['failed_requests'] == 1
assert failed['episodes'][0]['model_usage']['requests_without_usage'] == ['0105']
success = report(Path('artifacts/classify-language-dev20-local-v7-worker1'))
assert success['mean_score'] == 100 and success['all_valid_native_terminal']
assert success['total_actions'] == 1021 and success['successes'] == 1
interrupted = report(Path("artifacts/classify-language-frozen25-repeat1"))
assert interrupted["total_wall_seconds"] is None
assert interrupted["mean_score"] is None and not interrupted["all_valid_native_terminal"]
assert interrupted["episodes"][0]["nonterminal_snapshot_score"] is None
assert interrupted["episodes"][0]["unaccepted_native_terminal_score"] == 40
print('Interrupted snapshot excluded; independent native100 retained with API failure accounting')
