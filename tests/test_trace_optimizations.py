"""Transport, evidence preservation and unambiguous assessment counters."""
from copy import deepcopy
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from source_feed_adapters import run_curl
from source_identity import SourceSelectionError
from quality_review import compact_source_packet, compact_review_source_packet
from event_editorial_runtime import assessment_counts, evaluate_candidates
from event_editorial import checked_policy

XML = b'<?xml version="1.0"?><rss><channel><item><title>Real feed</title></item></channel></rss>'


class SourceTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                cls.requests.append(self.path)
                self.send_response(503 if self.path == '/error' else 200)
                if self.path == '/encoded':
                    self.send_header('Content-Encoding', 'gzip')
                self.end_headers()
                body = XML if self.path == '/plain' else gzip.compress(XML)
                if self.path == '/broken': body = body[:15]
                if self.path == '/invalid_deflate': body = body[:10] + b'\xff'*20
                if self.path == '/error': body = b'<html>Unavailable</html>'
                self.wfile.write(body)
            def log_message(self, *args): pass
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def fetch(self, route, output, attempts=1):
        return run_curl(f'http://127.0.0.1:{self.server.server_port}{route}', output,
                        attempts=attempts, max_time_seconds=3)

    def test_plain_and_both_gzip_encodings_are_parsed_and_saved_as_xml(self):
        with tempfile.TemporaryDirectory() as folder:
            for route in ('/plain', '/encoded', '/missing_encoding'):
                with self.subTest(route=route):
                    path = Path(folder)/'source.xml'
                    text = self.fetch(route, path)
                    self.assertEqual(ET.fromstring(text).findtext('./channel/item/title'), 'Real feed')
                    self.assertEqual(path.read_bytes(), XML)

    def test_http_failure_does_not_reuse_stale_success_and_honors_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'source.xml'; path.write_bytes(XML)
            before = self.requests.count('/error')
            with self.assertRaises(SourceSelectionError): self.fetch('/error', path, attempts=2)
            self.assertEqual(self.requests.count('/error') - before, 2)

    def test_broken_gzip_is_a_fetch_error_not_replacement_text(self):
        with tempfile.TemporaryDirectory() as folder:
            for route in ('/broken','/invalid_deflate'):
                with self.subTest(route=route), self.assertRaisesRegex(SourceSelectionError, 'decode failed'):
                    self.fetch(route, Path(folder)/'source.xml')


class ReviewProjectionTests(unittest.TestCase):
    def material(self):
        quote = 'A separate source reports 8 to 9 million; not a court finding.'
        proof = {'source_id': 'secondary', 'url': 'https://secondary.example/report',
                 'quote': quote, 'read_status': 'read', 'quote_sha256': 'a'*64,
                 'source_text_sha256': 'b'*64, 'assessment_input_sha256': 'c'*64}
        return {'title':'中文研究', 'primary_url':'https://primary.example/report',
                'source_text': 'Body does not mention the number. ' + 'x'*12000,
                'fallback_excerpt':'发现摘要中的数字尚未独立证实。', 'discovery_url':'https://feed.example/item',
                'fetch_status':'fallback_after_fetch_error',
                'supporting_evidence':[proof, deepcopy(proof), {**proof, 'source_id':'third',
                    'url':'https://third.example/report', 'read_status':'unread'}]}

    def test_projection_keeps_all_text_boundaries_and_source_identity(self):
        material=self.material(); original=deepcopy(material)
        full=compact_source_packet({'news':[material]})['news'][0]
        small=compact_review_source_packet({'news':[material]})['news'][0]
        self.assertEqual(material,original)
        for key in ('source_text','material_excerpt','title','primary_url'):
            self.assertEqual(full[key],small[key])
        for kind in ('document','summary'):
            before=full['source_evidence_provenance'][kind]
            after=small['source_evidence_provenance'][kind]
            for key in ('id','url','role','fetch_status','truncated','independently_verified'):
                self.assertEqual(before.get(key),after.get(key))
        self.assertTrue(small['source_evidence_provenance']['document']['truncated'])
        self.assertEqual(small['source_evidence_provenance']['document']['role'],'unverified_excerpt')
        self.assertFalse(small['source_evidence_provenance']['summary']['independently_verified'])
        self.assertEqual(len(small['supporting_evidence']),2)
        self.assertEqual(small['supporting_evidence'][0]['quote'],material['supporting_evidence'][0]['quote'])
        self.assertEqual(small['supporting_evidence'][1]['url'],'https://third.example/report')
        self.assertEqual(small['supporting_evidence'][1]['read_status'],'unread')
        self.assertNotIn('a'*64,json.dumps(small))

    def test_empty_and_no_supporting_evidence_preserve_legacy_interface(self):
        self.assertEqual(compact_review_source_packet({}),{'research':[],'news':[]})
        item=self.material(); item['supporting_evidence']=[]
        full=compact_source_packet({'research':[item]})['research'][0]
        self.assertIn('sha256',full['source_evidence_provenance']['document'])
        self.assertEqual(compact_review_source_packet({'research':[item]})['research'][0]['supporting_evidence'],[])


class AssessmentCounterTests(unittest.TestCase):
    def test_unassessed_and_editorially_deferred_are_disjoint(self):
        policy=checked_policy({})
        base={'assessment_status':'complete','score':80,'evidence_status':'supported',
              'novelty':'new','editorial_recommendation':'include','decision_reason':'fixture'}
        rows=[{'editorial_assessment':a} for a in [base,
              {**base,'assessment_status':'pending','score':None},
              {**base,'evidence_status':'unresolved'},
              {**base,'novelty':'repeat'}]]
        counts=assessment_counts(rows,policy)
        self.assertEqual(counts,{'assessment_completed':3,'assessment_pending':1,
                                'editorial_deferred':1,'completed':3,'pending':2})
        self.assertEqual(counts['assessment_completed']+counts['assessment_pending'],len(rows))

    def test_empty_runtime_persists_explicit_zero_counts_without_model(self):
        with tempfile.TemporaryDirectory() as folder:
            rows,summary=evaluate_candidates([],output_dir=Path(folder))
            self.assertEqual(rows,[])
            for key in ('assessment_completed','assessment_pending','editorial_deferred'):
                self.assertEqual(summary[key],0)
            saved=json.loads((Path(folder)/'events.json').read_text())
            self.assertEqual(saved['event_editorial'],summary)


if __name__ == '__main__': unittest.main()
