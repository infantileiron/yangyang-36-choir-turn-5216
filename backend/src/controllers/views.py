from src.constants.enums import SingerState
from src.utils.formatting import timestamp

STATE_NAME = {
    SingerState.PAIRED: '可入样',
    SingerState.MISSING: '缺读数',
    SingerState.BLOCKED: '冲突搁置',
}


def review_view(item):
    return {'decision': item.decision, 'note': item.note, 'reviewer': item.reviewer,
            'at': timestamp(item.created_at)} if item else None


def run_view(item, review=None, *, include_result=False):
    view = {'id': item.id, 'title': item.title, 'owner': item.owner, 'status': item.status,
            'parameters': item.parameters, 'sample_size': item.sample_size,
            'excluded_count': len(item.exclusions or []),
            'snapshot_id': item.last_reading_id, 'created_at': timestamp(item.created_at),
            'finished_at': timestamp(item.finished_at), 'review': review_view(review),
            'error': item.error}
    if include_result:
        view['result'] = item.result
        view['exclusions'] = item.exclusions
    return view


def ledger_view(entry):
    active = entry['active']
    usual = active.get('usual')
    new = active.get('new')
    return {
        'singer': entry['singer'], 'part': entry['part'],
        'state': entry['state'], 'state_label': STATE_NAME[entry['state']],
        'conflict_cues': entry['conflict_cues'], 'missing_cues': entry['missing_cues'],
        'usual': usual['deviation'] if usual else None,
        'usual_version': usual['version'] if usual else None,
        'new': new['deviation'] if new else None,
        'new_version': new['version'] if new else None,
    }


def schedule_view(item):
    return {'id': item.id, 'run_id': item.run_id, 'recipient': item.recipient,
            'interval_minutes': item.interval_minutes, 'next_at': timestamp(item.next_at),
            'active': item.active}


def delivery_view(delivery, schedule):
    return {'id': delivery.id, 'schedule_id': schedule.id, 'recipient': schedule.recipient,
            'run_id': schedule.run_id, 'at': timestamp(delivery.delivered_at),
            'scheduled_at': timestamp(delivery.scheduled_at)}
