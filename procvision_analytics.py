#!/usr/bin/env python3
"""ProcVision G17 read-only analytics add-on for the existing /api/snapshot endpoint."""
import argparse
import csv
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen


def fetch_snapshot(url):
    with urlopen(url, timeout=5) as response:
        data = json.load(response)
    if not isinstance(data, dict) or not isinstance(data.get('processes'), list):
        raise ValueError('Unexpected snapshot format')
    return data


def summarize(snapshot):
    processes = snapshot['processes']
    states = Counter(str(p.get('state', '?'))[:1] for p in processes)
    ranked = sorted(processes, key=lambda p: float(p.get('cpu', 0)), reverse=True)[:5]
    return states, ranked


def collect(url, count, interval, output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = ['time_utc', 'cpu_percent', 'memory_percent', 'process_count',
              'running', 'sleeping', 'disk_sleep', 'stopped', 'zombie',
              'top_pid', 'top_name', 'top_cpu_percent']
    with output.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for i in range(count):
            snapshot = fetch_snapshot(url)
            states, ranked = summarize(snapshot)
            top = ranked[0] if ranked else {}
            row = {
                'time_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                'cpu_percent': snapshot.get('cpu', 0),
                'memory_percent': snapshot.get('memory', {}).get('percent', 0),
                'process_count': snapshot.get('count', len(snapshot['processes'])),
                'running': states['R'], 'sleeping': states['S'],
                'disk_sleep': states['D'], 'stopped': states['T'] + states['t'],
                'zombie': states['Z'], 'top_pid': top.get('pid', ''),
                'top_name': top.get('name', ''),
                'top_cpu_percent': top.get('cpu', '')
            }
            writer.writerow(row)
            handle.flush()
            print('Sample {}/{} | CPU {}% | RAM {}% | R={} S={} Z={} | Top: {} ({})'.format(
                i + 1, count, row['cpu_percent'], row['memory_percent'],
                row['running'], row['sleeping'], row['zombie'],
                row['top_name'], row['top_pid']))
            if i + 1 < count:
                time.sleep(interval)
    print('Saved:', output.resolve())


def main():
    parser = argparse.ArgumentParser(description='Read-only ProcVision analytics and CSV export')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--samples', type=int, default=30)
    parser.add_argument('--interval', type=float, default=1)
    parser.add_argument('--output', default='results/advanced_analytics.csv')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or args.samples < 1 or args.interval <= 0:
        parser.error('Port, samples, and interval must be positive and valid')
    try:
        collect('http://127.0.0.1:{}/api/snapshot'.format(args.port),
                args.samples, args.interval, args.output)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(1, 'Cannot collect data: {}\nCheck that ProcVision is running on the selected port.\n'.format(exc))


if __name__ == '__main__':
    main()
