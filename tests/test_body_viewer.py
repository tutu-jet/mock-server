import json
import unittest
from html.parser import HTMLParser

from app.flows import FlowRecord, MAX_BODY_BYTES
from web import routes
from web.body_viewer import reading_json, reading_text

FENCE = chr(96) * 3


class Content(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.text = []
        self.buttons = []
        self.details = []
        self.summary = None
        self.feed(html)

    def handle_data(self, data):
        self.text.append(data)
        if self.summary is not None:
            self.summary["text"] += data

    def handle_starttag(self, tag, attrs):
        if tag == 'button':
            self.buttons.append(dict(attrs))
        if tag == 'details':
            self.details.append({"attrs": dict(attrs), "text": ""})
        elif tag == 'summary':
            self.summary = self.details[-1]

    def handle_endtag(self, tag):
        if tag == 'summary':
            self.summary = None


class BodyViewerTest(unittest.TestCase):
    def test_equal_message_fields_collapse_only_anonymized_content(self):
        for raw in ('', 'short', 'hello\nworld'):
            body = json.dumps({'messages': [{'anonymized_content': raw, 'content': raw}]})
            tree = reading_json(body)
            message = tree['children'][0][1]['children'][0][1]
            fields = dict(message['children'])
            self.assertEqual(fields['anonymized_content']['duplicate_of'], 'content')
            self.assertNotIn('duplicate_of', fields['content'])
            html = self.render_flow(body)
            content = Content(html)
            duplicate = next(item for item in content.details if 'anonymized_content' in item['text'])
            self.assertNotIn('open', duplicate['attrs'])
            self.assertIn('与 content 相同', duplicate['text'])
            originals = [item for item in content.details if item['text'].startswith('content ')]
            self.assertEqual(len(originals), 1)
            self.assertIn('open', originals[0]['attrs'])
            copies = [json.loads(button['data-copy-content']) for button in content.buttons
                      if 'data-copy-content' in button]
            self.assertIn(raw, copies)
            self.assertIn(routes._pretty_json(body), ''.join(content.text))

    def test_anonymized_content_stays_collapsed_even_when_different_or_unpaired(self):
        for message in (
            {'anonymized_content': 'masked\ntext', 'content': 'original\ntext'},
            {'anonymized_content': 'text\n', 'content': 'text'},
            {'anonymized_content': 'hello\nworld', 'other': 'hello\nworld'},
            {'anonymized_content': 'masked', 'content': 'original'},
            {'anonymized_content': None, 'content': None},
        ):
            body = json.dumps(message)
            fields = dict(reading_json(body)['children'])
            self.assertNotIn('duplicate_of', fields['anonymized_content'])
            content = Content(self.render_flow(body))
            self.assertNotIn('与 content 相同', ''.join(content.text))
            for item in content.details:
                if item['text'].startswith('anonymized_content '):
                    self.assertNotIn('open', item['attrs'])
                else:
                    self.assertIn('open', item['attrs'])

    def test_decodes_only_outer_json_and_preserves_fences_and_literal_backslashes(self):
        inner = {'list': [{'title': 'Certificate', 'path': r'C:\temp'}]}
        raw = '\n' + FENCE + 'list-show\n' + json.dumps(inner) + '\n' + FENCE + '\n'
        tree = reading_json(json.dumps({'anonymized_content': raw}))
        value = tree['children'][0][1]
        self.assertEqual(value['raw'], raw)
        self.assertTrue(value['text'].startswith('\n' + FENCE + 'list-show\n{\n'))
        self.assertTrue(value['text'].endswith('\n' + FENCE + '\n'))
        code = value['text'].split(FENCE + 'list-show\n')[1].split('\n' + FENCE)[0]
        self.assertEqual(json.loads(code), inner)
        literal = r'C:\temp\n is literal, "quoted"'
        self.assertEqual(reading_json(json.dumps(literal))['text'], literal)

    def test_fence_formatting_keeps_prose_markers_and_non_json_code(self):
        raw = 'Before\r\n' + FENCE + 'list-show\r\n{"x":1}\r\n' + FENCE + '\r\nAfter'
        expected = 'Before\r\n' + FENCE + 'list-show\r\n{\r\n  "x": 1\r\n}\r\n' + FENCE + '\r\nAfter'
        self.assertEqual(reading_text(raw), expected)
        for text in (FENCE + 'text\nhello "world"\n' + FENCE,
                     FENCE + 'list-show\n{"broken":\n' + FENCE,
                     FENCE + 'list-show\n{"x":1}', 'plain <b>text</b>'):
            self.assertEqual(reading_text(text), text)

    def render_flow(self, body, truncated=False):
        record = FlowRecord(id=42, ts=0, method='GET', url='https://example.test',
                            status=200, duration_ms=10, resp_body=body,
                            resp_body_truncated=truncated, resp_body_size=MAX_BODY_BYTES + 100)
        return routes.env.get_template('_flow_detail.html').render(flow=record)

    def test_reader_escapes_markup_and_copy_uses_exact_unformatted_field(self):
        raw = '\n' + FENCE + 'list-show\n{"title":"<script>alert(1)</script>"}\n' + FENCE + '\n'
        body = json.dumps({'anonymized_content': raw, 'empty': '', 'flag': False, 'nothing': None})
        html = self.render_flow(body)
        content = Content(html)
        self.assertNotIn('<script>', html)
        button = next(button for button in content.buttons if 'data-copy-content' in button)
        self.assertEqual(json.loads(button['data-copy-content']), raw)
        self.assertIn(reading_text(raw), ''.join(content.text))
        self.assertIn(routes._pretty_json(body), ''.join(content.text))
        self.assertIn('id="flow-response-body-json"', html)
        self.assertIn('hidden', html)
        self.assertIn("copyFlowBody(42, 'resp', this)", html)
        self.assertIn('href="/flows/42/body/resp" download', html)

    def test_truncated_even_valid_json_and_non_json_fall_back_to_original(self):
        for body, truncated in (('{"x":1}', True), ('<b>raw</b>', False), ('{"x":', False)):
            html = self.render_flow(body, truncated)
            self.assertNotIn('data-body-mode=', html)
            self.assertIn(body, ''.join(Content(html).text))
            if truncated:
                self.assertIn('--- 已截断', html)

    def test_scalars_empty_containers_and_deep_json_do_not_break_viewer(self):
        for body in ('null', 'false', '0', '"hello"', '{}', '[]', '[' * 10 + '0' + ']' * 10):
            self.assertIn('data-body-mode="reading"', self.render_flow(body))
        too_deep = '[' * 30 + '0' + ']' * 30
        self.assertIsNone(reading_json(too_deep))
        self.assertNotIn('data-body-mode=', self.render_flow(too_deep))


if __name__ == '__main__':
    unittest.main()
