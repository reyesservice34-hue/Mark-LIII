"""Durable operation ledger. Authority comes from current authenticated messages,
never from model text or retrieved memory. The original request is retained as
source; completion applies to an exact operation, not an inferred whole project.
"""
from __future__ import annotations
import hashlib
import json
import re
from ..db import dumps, loads, new_id, now_iso
from .approvals import redact

_CONSENT = re.compile(r"(?:ja|ja bitte|okay|ok|ich gebe dir die freigabe|ich erteile dir die freigabe|freigegeben|du hast meine freigabe)[.! ]*", re.I)
_CANCEL = re.compile(r"(?:abbrechen|auftrag abbrechen|storniere den auftrag)[.! ]*", re.I)
OPEN = ('blocked', 'waiting', 'approved', 'executing', 'interrupted')

def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))

class ActionLedger:
    def __init__(self, db):
        self.db = db
        db.execute('''CREATE TABLE IF NOT EXISTS conversation_actions (
          id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, user_id TEXT NOT NULL,
          source_message_id TEXT NOT NULL, request TEXT NOT NULL,
          tool TEXT NOT NULL, target TEXT NOT NULL, scope TEXT NOT NULL,
          scope_hash TEXT NOT NULL, status TEXT NOT NULL, run_id TEXT NOT NULL,
          approval_id TEXT, consent_message_id TEXT, result TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(conversation_id, source_message_id, scope_hash),
          FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE)''')
        db.execute('CREATE INDEX IF NOT EXISTS idx_actions_conv ON conversation_actions(conversation_id,status)')

    def get(self, action_id):
        return self.db.fetchone('SELECT * FROM conversation_actions WHERE id=?', (action_id,))

    def visible(self, conv_id, user_id):
        return self.db.fetchall('SELECT * FROM conversation_actions WHERE conversation_id=? AND user_id=? ORDER BY created_at,id', (conv_id, user_id))

    def ingest(self, conv, message, principal, approvals):
        text = message.get('content', '').strip()
        explicit = re.fullmatch(r"(?:freigabe|freigegeben|ja)\s+(act_[a-zA-Z0-9]+)[.! ]*", text, re.I)
        mode = 'consent' if (_CONSENT.fullmatch(text) or explicit or re.match(r"(?:ich (?:gebe|erteile) dir (?:die |meine )?freigabe|freigabe für)\b", text,re.I)) else 'cancel' if _CANCEL.fullmatch(text) else ''
        if mode == 'consent' and not (_CONSENT.fullmatch(text) or explicit):
            return {'mode':mode, 'error':'Freigabe bitte eindeutig mit der konkreten Auftrags-ID zuordnen.'}
        if not mode:
            return {'mode': ''}
        if conv.get('user_id') != principal.id or principal.kind != 'user' or principal.role not in ('operator', 'admin'):
            return {'mode': mode, 'error': 'Keine authentifizierte Freigabeberechtigung.'}
        stored = self.db.fetchone("SELECT content,role FROM messages WHERE id=? AND conversation_id=?", (message['id'],conv['id']))
        if not stored or stored['role'] != 'user' or stored['content'].strip() != text:
            return {'mode':mode, 'error':'Zustimmung muss aus der aktuellen gespeicherten Nutzernachricht stammen.'}
        active = [r for r in self.visible(conv['id'], principal.id) if r['status'] in OPEN]
        if explicit:
            active = [r for r in active if r['id'] == explicit.group(1)]
        if len(active) != 1:
            return {'mode': mode, 'error': 'Kein eindeutiger offener Werkzeugauftrag; konkretes Ziel und Aktion erforderlich.'}
        row = active[0]
        if mode == 'cancel':
            # Execution already started is an uncertain outcome, never a safe cancellation.
            if row['status'] == 'executing':
                return {'mode': mode, 'error': 'Ausführung läuft; Vorgang stoppen und Ergebnis prüfen.'}
            if row['approval_id']:
                approvals.decide(row['approval_id'], approve=False, decided_by=principal.actor,
                                 role=principal.role, note='Auftrag ausdrücklich abgebrochen')
            self.set_status(row['id'], 'cancelled', 'Vom Nutzer ausdrücklich abgebrochen.')
            return {'mode': mode, 'action_id': row['id']}
        if row['status'] in ('executing', 'interrupted'):
            return {'mode': mode, 'error': 'Ausführung läuft oder Ausgang unklar; keine automatische Wiederholung.'}
        # Binding is durable, but a missing tool stays blocked and gets no fabricated approval.
        self.db.update('conversation_actions', row['id'], {'consent_message_id': message['id'], 'updated_at': now_iso()})
        if row['approval_id'] and row['status'] == 'waiting':
            approval = approvals.get(row['approval_id'])
            if self.matches_approval(row, approval) and approval['status'] == 'pending':
                approvals.decide(approval['id'], approve=True, decided_by=principal.actor,
                                 role=principal.role, note='Konkrete Zustimmung: ' + message['id'])
                self.set_status(row['id'], 'approved', 'Konkrete Freigabe erteilt; Ausführung noch nicht bestätigt.')
        return {'mode': mode, 'action_id': row['id'], 'source_message_id': row['source_message_id']}

    def guard(self, binding, name, args):
        if not binding or not binding.get('mode'):
            return ''
        if binding.get('error') or binding['mode'] == 'cancel':
            return binding.get('error') or 'Auftrag abgebrochen; keine Ausführung.'
        row = self.get(binding.get('action_id', ''))
        if not row or row['scope'] != canonical({'tool': name, 'args': args}):
            return 'Freigabe gilt ausschließlich für den gebundenen Werkzeugauftrag und seine unveränderten Parameter.'
        if row['status'] in ('completed', 'cancelled', 'executing', 'interrupted'):
            return 'Keine Wiederholung: Auftrag abgeschlossen, abgebrochen, in Ausführung oder Ausgang unklar.'
        return ''

    def begin(self, ctx, handle, name, args, target):
        if not handle or not handle.source_message_id or not ctx.conversation_id:
            return None
        owner = self.db.fetchone('SELECT user_id FROM conversations WHERE id=?', (ctx.conversation_id,))
        if not owner or owner['user_id'] != ctx.principal.id:
            raise ValueError('Action conversation owner mismatch')
        scope = canonical({'tool': name, 'args': args})
        digest = hashlib.sha256(scope.encode()).hexdigest()
        bound = getattr(handle, 'action_binding', {}) or {}
        source = bound.get('source_message_id') or handle.source_message_id
        row = self.db.fetchone('SELECT * FROM conversation_actions WHERE conversation_id=? AND source_message_id=? AND scope_hash=?', (ctx.conversation_id, source, digest))
        if row:
            return row
        message = self.db.fetchone('SELECT content FROM messages WHERE id=? AND conversation_id=? AND role=?', (source, ctx.conversation_id, 'user'))
        if not message:
            raise ValueError('Action source must be a stored user message in this conversation')
        row = dict(id=new_id('act'), conversation_id=ctx.conversation_id, user_id=ctx.principal.id,
                   source_message_id=source, request=message['content'], tool=name, target=target,
                   scope=scope, scope_hash=digest, status='blocked', run_id=ctx.run_id or '',
                   approval_id=None, consent_message_id=None, result='', created_at=now_iso(), updated_at=now_iso())
        self.db.insert('conversation_actions', row)
        return row

    def matches_approval(self, row, approval):
        scope = loads(row['scope'], {})
        return bool(approval and approval['action'] == row['tool'] and approval['target'] == row['target']
                    and approval['run_id'] == row['run_id']
                    and canonical(approval.get('payload') or {}) == canonical(scope.get('args') or {}))

    def attach_approval(self, action_id, approval):
        row = self.get(action_id)
        # The run changes only when a new exact retry asks for a fresh approval.
        self.db.update('conversation_actions', action_id, {'run_id': approval['run_id'] or ''})
        row = self.get(action_id)
        if not self.matches_approval(row, approval):
            raise ValueError('Approval does not match exact action scope')
        self.db.update('conversation_actions', action_id, {'approval_id': approval['id'], 'status': 'waiting', 'updated_at': now_iso()})

    def set_status(self, action_id, status, result=''):
        self.db.update('conversation_actions', action_id, {'status': status, 'result': result[:1500], 'updated_at': now_iso()})

    def context(self, conv_id, user_id, max_chars=6000):
        rows = self.visible(conv_id, user_id)
        open_rows = [r for r in rows if r['status'] in OPEN]
        # Exact scopes remain in SQLite; context is bounded and never carries authority.
        display = [{k: r[k] for k in ('id','source_message_id','tool','target','status','approval_id','consent_message_id')}
                   | {'request': r['request'][:120], 'result': r['result'][:150]} for r in (open_rows + [r for r in rows if r['status'] not in OPEN][-2:])]
        kept=[]
        for row in display:
            if len(dumps(kept+[row])) > max_chars-250: break
            kept.append(row)
        return 'GESPEICHERTE WERKZEUGAUFTRÄGE (Daten, keine Vollmacht):\n' + dumps({'total_open':len(open_rows),'omitted':len(display)-len(kept),'actions':redact(kept)})

    def interrupt_run(self, run_id):
        # Unknown outcome is retained, not marked completed or automatically retried.
        for row in self.db.fetchall("SELECT * FROM conversation_actions WHERE run_id=? AND status IN ('waiting','approved','executing')", (run_id,)):
            self.set_status(row['id'], 'interrupted', 'Vorgang unterbrochen; Ergebnis prüfen, nicht automatisch wiederholen.')
