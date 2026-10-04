"""Version-bound host proposals. Host reports are NOT provider usage receipts."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .ai_gateway import redact_value
from .database import json_dumps, utc_now
from .errors import AppError
from .repository import Repository, make_id


def digest(value: Any) -> str:
    return 'sha256:' + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


class HostCollaboration:
    def __init__(self, repository: Repository):
        self.repository = repository
        self.database = repository.database

    @staticmethod
    def _context(connection: Any, project_id: str, route: str, step_index: int) -> dict[str, Any]:
        row = connection.execute('SELECT id,name,route,active_version_id FROM projects WHERE id=?', (project_id,)).fetchone()
        if row is None:
            raise AppError('project_not_found', '项目不存在', 404)
        project = dict(row)
        if project['route'] != route:
            raise AppError('project_route_conflict', '项目路线已变化，请重新发起协作', 409)
        version = connection.execute('SELECT id,version,artifact_digest FROM skill_versions WHERE id=? AND project_id=?', (project['active_version_id'], project_id)).fetchone()
        steps = [dict(item) for item in connection.execute('SELECT step_index,payload_json FROM step_states WHERE project_id=? AND route=? ORDER BY step_index', (project_id, route))]
        for item in steps:
            item['payload'] = json.loads(item.pop('payload_json'))
            item['payload'].pop('_ai', None)  # Acknowledging a proposal does not change business content.
        return redact_value({'project': project, 'route': route, 'step_index': step_index, 'steps': steps, 'artifact': dict(version) if version else None})

    def create(self, project_id: str, payload: dict[str, Any], actor_id: str = 'unknown', auth_mode: str = 'unknown') -> dict[str, Any]:
        route = payload.get('route')
        index = payload.get('step_index')
        if route not in {'template', 'existing'} or type(index) is not int or not 0 <= index < (9 if route == 'template' else 7):
            raise AppError('invalid_run_scope', '请选择有效路线和步骤')
        instruction = payload.get('instruction')
        if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 8000:
            raise AppError('invalid_host_instruction', '请填写 1–8000 字的协作要求')
        instruction = redact_value(instruction.strip())
        request_id, now = make_id('host'), utc_now()
        with self.database.transaction() as connection:
            context = self._context(connection, project_id, route, index)
            context['instruction'] = instruction
            if len(json_dumps(context).encode('utf-8')) > 100_000:
                raise AppError('context_too_large', '协作上下文超过 100 KB 限制', 413)
            context_hash = digest(context)
            connection.execute('INSERT INTO host_requests(id,project_id,route,step_index,context_hash,context_json,instruction,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
                               (request_id, project_id, route, index, context_hash, json_dumps(context), instruction, now, now))
            self.repository._audit(connection, project_id, 'user', 'host.request.created', 'host_request', request_id, {'context_hash': context_hash, 'route': route, 'step_index': index, 'actor_id': actor_id, 'auth_mode': auth_mode})
        return self.get(project_id, request_id)

    @staticmethod
    def _decode(row: Any) -> dict[str, Any]:
        record = dict(row)
        record['context'] = json.loads(record.pop('context_json'))
        record['result'] = json.loads(record.pop('result_json') or 'null')
        record.pop('result_hash', None)
        if record['result']:
            record.update(record['result'])
        record['automatically_invokes_host'] = False
        record['applied'] = False
        return record

    def _view(self, connection: Any, row: Any) -> dict[str, Any]:
        record = self._decode(row)
        try:
            current = self._context(connection, record['project_id'], record['route'], record['step_index'])
            current['instruction'] = record['instruction']
            record['context_current'] = digest(current) == record['context_hash']
        except AppError:
            record['context_current'] = False
        record['is_stale'] = not record['context_current']
        return record

    def get(self, project_id: str, request_id: str) -> dict[str, Any]:
        with self.database.session() as connection:
            row = connection.execute('SELECT * FROM host_requests WHERE id=? AND project_id=?', (request_id, project_id)).fetchone()
            if row is None:
                raise AppError('host_request_not_found', '协作请求不存在', 404)
            return self._view(connection, row)

    def list(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.session() as connection:
            rows = connection.execute('SELECT * FROM host_requests WHERE project_id=? ORDER BY created_at DESC,rowid DESC LIMIT 50', (project_id,)).fetchall()
            records = [self._view(connection, row) for row in rows]
        for record in records:
            record.pop('context', None)  # Full context is available through the per-request read endpoint.
        return records

    def submit(self, project_id: str, request_id: str, payload: dict[str, Any], actor_id: str = 'unknown', auth_mode: str = 'unknown') -> dict[str, Any]:
        output = payload.get('output')
        if not isinstance(output, dict) or not isinstance(output.get('summary'), str) or not output['summary'].strip():
            raise AppError('invalid_host_output', '宿主必须返回非空的建议内容')
        if len(json_dumps(output).encode('utf-8')) > 80_000:
            raise AppError('host_output_too_large', '协作结果超过 80 KB 限制', 413)
        if 'patch' in output and not isinstance(output['patch'], dict):
            raise AppError('invalid_host_output', '建议字段 patch 必须为对象')
        usage = payload.get('usage')
        counts = {key: None for key in ('input_tokens', 'output_tokens', 'total_tokens')}
        if usage is not None:
            if not isinstance(usage, dict):
                raise AppError('invalid_host_usage', '宿主用量必须为对象')
            for key in counts:
                value = usage.get(key)
                if value is not None and (type(value) is not int or value < 0 or value > 10**12):
                    raise AppError('invalid_host_usage', '宿主 Token 必须是非负整数或未知')
                counts[key] = value
            if all(value is not None for value in counts.values()) and counts['input_tokens'] + counts['output_tokens'] != counts['total_tokens']:
                raise AppError('invalid_host_usage', '宿主 Token 合计不一致')
        result = {'output': redact_value(output), 'host_label': str(payload.get('host_label') or '未标识宿主')[:120],
                  'model': str(payload.get('model') or 'unknown')[:120], 'source': 'host_writeback',
                  'provider_verified': False, 'usage_status': 'host_reported' if any(v is not None for v in counts.values()) else 'unknown',
                  'usage_source': 'host_reported' if any(v is not None for v in counts.values()) else 'unavailable', **counts}
        result_hash = digest(result)
        with self.database.transaction() as connection:
            row = connection.execute('SELECT * FROM host_requests WHERE id=? AND project_id=?', (request_id, project_id)).fetchone()
            if row is None:
                raise AppError('host_request_not_found', '协作请求不存在', 404)
            if payload.get('context_hash') != row['context_hash']:
                raise AppError('host_context_conflict', '回写摘要与原始请求不一致', 409)
            if row['status'] == 'proposed':
                if row['result_hash'] == result_hash:
                    return self._view(connection, row)
                raise AppError('host_result_conflict', '该请求已有结果，不能覆盖；请发起新请求', 409)
            current = self._context(connection, project_id, row['route'], row['step_index'])
            current['instruction'] = row['instruction']
            if digest(current) != row['context_hash']:
                raise AppError('host_context_stale', '步骤或版本已改变，请重新发起协作以避免写入过时结果', 409)
            connection.execute("UPDATE host_requests SET status='proposed',result_json=?,result_hash=?,updated_at=? WHERE id=?", (json_dumps(result), result_hash, utc_now(), request_id))
            self.repository._audit(connection, project_id, 'agent', 'host.result.proposed', 'host_request', request_id, {'context_hash': row['context_hash'], 'result_hash': result_hash, 'usage_status': result['usage_status'], 'provider_verified': False, 'actor_id': actor_id, 'auth_mode': auth_mode})
        return self.get(project_id, request_id)
