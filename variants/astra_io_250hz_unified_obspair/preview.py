"""Preview the first two decisions from a completed unified 250 Hz run."""
import argparse
import json
from pathlib import Path

from export_swift import records_for, sample_for


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    records = records_for(args.run.resolve())[:2]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    for record in records:
        sample = sample_for(output, record, copy_images=True)
        (output / f"{record['stem']}.json").write_text(json.dumps(sample, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'samples': len(records), 'output': str(output),
                      'scope': 'Actual unified 250 Hz decisions; no simulator or model calls'}, indent=2))


if __name__ == '__main__':
    main()
