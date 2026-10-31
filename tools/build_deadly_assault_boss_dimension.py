"""Export the reviewed Deadly Assault season and room catalogue."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLUMNS = (
    'combat_season', 'start_date', 'stage_slot', 'boss_zh', 'boss_en',
    'mech_regime', 'mech_tag', 'favored_archetype', 'source_url',
    'mechanism_code', 'boss_zh_canonical', 'raw_boss_aliases',
    'schedule_source_status', 'date_source_status', 'mechanism_source_status',
    'favored_archetype_basis', 'raw_source_url', 'mechanism_source_url',
)


def build(out: Path):
    catalogue = json.loads((ROOT / 'tools/deadly_assault_boss_catalog.json').read_text('utf-8'))
    rows = []
    for period in catalogue['periods']:
        for slot in period['stages']:
            boss = catalogue['bosses'][slot['boss_zh']]
            mechanism = catalogue['mechanisms'][slot['mechanism_code']]
            rows.append({
                'combat_season': period['combat_season'], 'start_date': period['start_date'],
                'stage_slot': slot['stage_slot'], 'boss_zh': slot['boss_zh'],
                'boss_en': boss['boss_en'], 'mech_regime': period['mech_regime'],
                'mech_tag': mechanism['mech_tag'],
                'favored_archetype': json.dumps(mechanism['favored_archetype'], ensure_ascii=False),
                'source_url': slot['source_url'], 'mechanism_code': slot['mechanism_code'],
                'boss_zh_canonical': boss.get('boss_zh_canonical', slot['boss_zh']),
                'raw_boss_aliases': json.dumps(boss['raw_boss_aliases'], ensure_ascii=False),
                'schedule_source_status': slot['schedule_source_status'],
                'date_source_status': period['date_source_status'],
                'mechanism_source_status': mechanism['source_status'],
                'favored_archetype_basis': 'mechanism_based_hypothesis_not_observed_share',
                'raw_source_url': slot['raw_source_url'],
                'mechanism_source_url': mechanism['source_url'],
            })
    keys = [(row['combat_season'], row['stage_slot']) for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError('Duplicate season-room keys in boss catalogue')
    if any(not {'S1', 'S2', 'S3'} <= {slot['stage_slot'] for slot in period['stages']}
           for period in catalogue['periods']):
        raise ValueError('Each listed season requires all three normal rooms')
    out.mkdir(parents=True, exist_ok=True)
    with (out / 'dim_deadly_assault_bosses.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({'seasons': len(catalogue['periods']), 'boss_room_rows': len(rows),
                      'normal_rooms': sum(row['stage_slot'] != 'ADV' for row in rows),
                      'advanced_rooms': sum(row['stage_slot'] == 'ADV' for row in rows)}, ensure_ascii=False))
    return rows


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dir', type=Path, default=ROOT / 'outputs/final_release')
    build(parser.parse_args().dir)
