"""Durable, operation-wide limits shared by all Acquisition jobs of one manual update.

The counter is a conservative reservation ledger: a slot is fsynced before a provider request can leave the
container. If the process stops between reservation and socket creation, that slot stays charged. This can leave
some of the configured allowance unused, but a restart can never reset it or send beyond the operation limit.
"""
import fcntl
import json
import os
import stat
import uuid
from pathlib import Path

from .profile import atomic_json


class OperationBudgetError(RuntimeError):
    pass


class OperationBudget:
    def __init__(self, root, operation_id, maximum):
        try:
            if str(uuid.UUID(operation_id)) != operation_id:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise OperationBudgetError('Invalid operation id') from None
        if type(maximum) is not int or not 1 <= maximum <= 50:
            raise OperationBudgetError('Invalid operation send limit')
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink() or not self.root.is_dir():
            raise OperationBudgetError('Unsafe operation ledger directory')
        self.operation_id = operation_id
        self.maximum = maximum
        self.path = self.root / (operation_id + '.json')
        self.lock_path = self.root / (operation_id + '.lock')

    def _read(self):
        if not self.path.exists():
            return dict(schema=1, operation_id=self.operation_id, maximum=self.maximum, sends=[], history_matches=[])
        if self.path.is_symlink():
            raise OperationBudgetError('Unsafe operation ledger')
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if (set(value) != {'schema', 'operation_id', 'maximum', 'sends', 'history_matches'}
                    or value['schema'] != 1 or value['operation_id'] != self.operation_id
                    or type(value['maximum']) is not int or value['maximum'] != self.maximum
                    or not isinstance(value['sends'], list) or len(value['sends']) > self.maximum
                    or not isinstance(value['history_matches'], list)
                    or len(set(value['history_matches'])) != len(value['history_matches'])
                    or value['history_matches'] != sorted(value['history_matches'])
                    or any(type(ident) is not int or ident <= 0 for ident in value['history_matches'])
                    or len(value['history_matches']) > 3
                    or any(not isinstance(item, dict)
                           or not {'job_id','channel','path','status'} <= set(item)
                           or not set(item) <= {'job_id','channel','path','status','http_status','error'}
                           or not isinstance(item['job_id'], str) or not item['job_id'] or len(item['job_id']) > 80
                           or not isinstance(item['channel'], str) or not item['channel'] or len(item['channel']) > 32
                           or not isinstance(item['path'], str) or len(item['path']) > 160
                           or item['status'] not in ('reserved','completed')
                           or (item['status']=='completed' and not {'http_status','error'} <= set(item))
                           or (item.get('http_status') is not None and type(item.get('http_status')) is not int)
                           or (item.get('error') is not None and not isinstance(item.get('error'),str))
                           for item in value['sends'])):
                raise ValueError()
            return value
        except (OSError, ValueError, TypeError, KeyError):
            raise OperationBudgetError('Operation ledger is missing, corrupt or belongs to another policy') from None

    def _change(self, callback):
        flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(self.lock_path, flags, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OperationBudgetError('Unsafe operation ledger lock')
            fcntl.flock(fd, fcntl.LOCK_EX)
            value = self._read()
            changed, result = callback(value)
            if changed:
                atomic_json(self.path, value)
            return result
        finally:
            os.close(fd)

    def reserve_send(self, *, job_id, channel, path):
        """Durably claim one physical-send slot; False means no provider request may be sent."""
        def reserve(value):
            if len(value['sends']) >= self.maximum:
                return False, dict(allowed=False, used=len(value['sends']), remaining=0)
            value['sends'].append(dict(job_id=job_id, channel=str(channel)[:32], path=str(path)[:160], status='reserved'))
            used = len(value['sends'])
            return True, dict(allowed=True, index=used - 1, used=used, remaining=self.maximum - used)
        return self._change(reserve)

    def complete_send(self, index, *, status=None, error=None):
        def finish(value):
            if type(index) is not int or not 0 <= index < len(value['sends']):
                raise OperationBudgetError('Unknown send reservation')
            item = value['sends'][index]
            if item['status'] != 'reserved':
                return False, None
            item['status'] = 'completed'
            item['http_status'] = status if type(status) is int else None
            item['error'] = str(error)[:120] if error is not None else None
            return True, None
        self._change(finish)

    def reserve_history(self, match_id, maximum=3):
        """Reserve one unique historical-recovery slot for this manual operation, across its batches/restarts."""
        if type(match_id) is not int or match_id <= 0 or type(maximum) is not int or not 0 <= maximum <= 3:
            raise OperationBudgetError('Invalid history recovery reservation')
        def reserve(value):
            if match_id in value['history_matches']:
                return False, True
            if len(value['history_matches']) >= maximum:
                return False, False
            value['history_matches'].append(match_id)
            value['history_matches'].sort()
            return True, True
        return self._change(reserve)

    def snapshot(self):
        def read(value):
            used = len(value['sends'])
            return False, dict(operation_id=self.operation_id, maximum=self.maximum, used=used,
                               remaining=self.maximum - used, history_matches=list(value['history_matches']),
                               conservative=True)
        return self._change(read)
