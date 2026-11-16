"""Loopback-only visual preview: actual Juno handlers/storage, fictional data,
scripted OpenAI responses, no credentials or external server requests.
"""
from __future__ import annotations
import argparse
from functools import partial
import json
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'tutor-prototype'))


def scripted_response(self, **request):
    messages=request['input']
    student_turns=[m['content'] for m in messages if m['role']=='user' and not m['content'].startswith('(the call')]
    mistake='I am working here since two weeks.'
    if request.get('text',{}).get('format'):
        report={
            'what_we_did':'In this fictional preview class, Alex practised answering Juno in complete sentences.',
            'corrections':[{'tier':'gentle_recast','said':mistake,'better':"I’ve been working here for two weeks.",'note':'Use the present perfect continuous with for to describe a duration.'}] if mistake in '\n'.join(student_turns[:-1]) else [],
            'word_traps':[], 'pronunciation':[],
            'vocabulary_learned':[{'term':'a little more time','meaning':'a short additional period of time'}],
            'what_went_well':['You kept the conversation going with a complete answer.'],
            'homework':['Make one more sentence about an imaginary situation using “a little more time”.'],
            'next_recommendation':'Practise describing how long an activity has continued.',
            'updated_recurring_error_patterns':[], 'grammar_point_taught':'',
            'skill_estimates':{'enough_evidence':False,'fluency':'B1','grammar':'B1','vocabulary':'B1','understanding':'B1'}}
        text=json.dumps(report)
    elif "They pressed 'Help me'" in request['instructions']:
        text='Juno te pide que cuentes un poco más sobre esta situación imaginaria.\n>> I need a little more time. || Necesito un poco más de tiempo.'
    elif 'Word tapped:' in messages[-1]['content']:
        text='Un poco más de tiempo, en este contexto.\nI need a little more time.'
    elif messages[-1]['content'].startswith('(the call'):
        text='Hello, Alex. Let’s practise an imaginary situation together. What would you like to talk about today?'
    elif mistake in messages[-1]['content']:
        text='A natural way to say that is “I’ve been working here for two weeks.” What do you enjoy about your imaginary job?'
    else:
        text='Thank you for sharing that. Imagine you need a little more time. How would you explain it?'
    usage=SimpleNamespace(input_tokens=120,output_tokens=40,input_tokens_details=SimpleNamespace(cached_tokens=0,cache_write_tokens=0))
    response=SimpleNamespace(status='completed',output_text=text,output=[],usage=usage)
    if request.get('stream'):
        def events():
            for piece in [text[:15],text[15:40],text[40:]]:
                yield SimpleNamespace(type='response.output_text.delta',delta=piece)
            yield SimpleNamespace(type='response.completed',response=response)
        return events()
    return response


def make_preview(root,dist=HERE/'dist'):
    import private_mac_test
    web,PrivateHandler=private_mac_test.create_application(root)
    import store
    # The preview has no live-service mode. The environment was replaced before
    # imports; only synthetic data in this new temporary SQLite file is allowed.
    if store.using_postgres() or not store.DB_PATH.is_relative_to(root):
        raise RuntimeError('Preview database isolation failed')
    store.MAX_CALLS_PER_DAY=0
    class PreviewHandler(PrivateHandler):
        def _handle_start(self,session,data):
            return web.Handler._handle_start(self,session,data)
        def do_GET(self):
            if not self._local_host():
                self.send_error(403);return
            if self.path=='/scenarios.json':
                return web.Handler.do_GET(self)
            relative=self.path.split('?',1)[0].lstrip('/')
            target=(dist/relative).resolve()
            if not target.is_relative_to(dist.resolve()):
                self.send_error(404);return
            if not relative or not target.is_file():
                target=dist/'index.html'
            if not target.exists():
                self.send_error(503,'Build the visual prototype first with npm run build');return
            import mimetypes
            self.send_response(200)
            self.send_header('Content-Type',mimetypes.guess_type(target)[0] or 'application/octet-stream')
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            body=target.read_bytes();self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def do_POST(self):
            if self.path=='/api/preview/reset':
                if not self._local_host() or self.headers.get('Origin')!=f'http://127.0.0.1:{self.server.server_port}':
                    self.send_error(403);return
                size=int(self.headers.get('Content-Length','0'));self.rfile.read(size)
                with store._LOCK:
                    connection=store.connect()
                    for table in ('calls','student_memory','usage_daily','spend_daily','ai_reservations','call_locks','turn_attempts','idempotency'):
                        connection.execute(f'DELETE FROM {table}')
                    connection.commit()
                web.SESSIONS.clear();web.DEFINE_CACHE.clear();self._load_session();self._send_json({'reset':True});return
            super().do_POST()
    return web,PreviewHandler


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--port',type=int,default=5400);args=parser.parse_args()
    # Even if a future edit bypasses the fake response, outbound API traffic is
    # refused. Browser speech is optional and only starts on an explicit click.
    original=socket.socket.connect
    def local_only(sock,address):
        if isinstance(address,tuple) and address[0] not in ('127.0.0.1','::1','localhost'):
            raise RuntimeError('External network forbidden in visual preview')
        return original(sock,address)
    with tempfile.TemporaryDirectory(prefix='juno-visual-fictional-') as directory:
        web,handler=make_preview(Path(directory))
        import ai
        with mock.patch.object(ai.Responses,'create',scripted_response),mock.patch.object(socket.socket,'connect',local_only):
            server=web.ThreadingHTTPServer(('127.0.0.1',args.port),handler)
            print(f'Fictional Juno visual preview: http://127.0.0.1:{server.server_port}',flush=True)
            print('No paid AI calls, student imports or production connection. Ctrl+C stops and discards preview data.',flush=True)
            try:server.serve_forever()
            except KeyboardInterrupt:pass
            finally:server.server_close()
if __name__=='__main__':main()
