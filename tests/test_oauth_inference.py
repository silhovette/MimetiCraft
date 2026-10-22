"""Network-free reconstruction / stream checks using fake OpenAI client."""
import json
from pathlib import Path
from types import SimpleNamespace as NS
import sys
import unittest
from unittest.mock import patch

from video2pack import _request_scene_text, infer_scene, run

class FakeStream:
    def __init__(self, events): self.events=events
    def __enter__(self): return iter(self.events)
    def __exit__(self,*args): pass

class InferTests(unittest.TestCase):
    def test_sdk_server_error_before_output_retries_once(self):
        from openai import APIError
        error = APIError('An error occurred. Request ID req_sdk_test',
                         request=NS(), body={'type':'server_error', 'message':'diagnostic'})
        class BrokenStream:
            response = NS(status_code=200, headers={'x-request-id':'req_header_test'})
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def __iter__(self):
                yield NS(type='response.created')
                raise error
        replies = [BrokenStream(), FakeStream([
            NS(type='response.output_text.delta', delta='{"ok":true}'),
            NS(type='response.completed', response=NS(output=[]))])]
        requests = []
        def create(**params):
            requests.append(params)
            return replies.pop(0)
        client = NS(responses=NS(create=create))
        with patch('video2pack.time.sleep') as sleep:
            result = _request_scene_text(client, {'stream':True, 'model':'m'}, 'm', 1000)
        self.assertEqual(result, '{"ok":true}')
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0], requests[1])
        sleep.assert_called_once_with(2.0)

    def test_partial_stream_failure_never_replays_or_overwrites_existing_zip(self):
        import tempfile
        from openai import APIError
        error = APIError('Server error req_partial_test', request=NS(),
                         body={'type':'server_error', 'message':'private request data'})
        calls = []
        class BrokenStream:
            response = NS(status_code=200, headers={'x-request-id':'req_partial_header'})
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def __iter__(self):
                yield NS(type='response.output_text.delta', delta='{"partial":')
                raise error
        class DummyOpenAI:
            def __init__(self, **params): self.responses = self
            def create(self, **params):
                calls.append(params)
                return BrokenStream()
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)/'existing.zip'
            out.write_bytes(b'previous-file')
            with patch('video2pack.video_frames', return_value=[(1,b'jpeg')]), \
                 patch.dict(sys.modules, {'openai':NS(OpenAI=DummyOpenAI)}), \
                 patch('video2pack.time.sleep') as sleep:
                with self.assertRaises(RuntimeError) as caught:
                    run(video='test.mp4', out=str(out), model='m', frames=2, access_token='private-token')
            self.assertEqual(len(calls), 1)
            sleep.assert_not_called()
            self.assertEqual(out.read_bytes(), b'previous-file')
            self.assertEqual(list(Path(temp).iterdir()), [out])
            message = str(caught.exception)
            self.assertIn('req_partial_header', message)
            self.assertIn('server_error', message)
            self.assertIn('already started', message)
            self.assertNotIn('private-token', message)
            self.assertNotIn('private request data', message)

    def test_plan_allowance_error_is_not_retried(self):
        calls = []
        def create(**params):
            calls.append(params)
            return FakeStream([NS(type='response.failed', response=NS(error=NS(
                code='subscription_sharing_usage_limit_exceeded')))])
        with patch('video2pack.time.sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError, 'subscription_sharing_usage_limit_exceeded'):
                _request_scene_text(NS(responses=NS(create=create)), {'stream':True}, 'm', 1000)
        self.assertEqual(len(calls), 1)
        sleep.assert_not_called()

    def test_repeated_server_errors_stop_after_two_attempts(self):
        calls = []
        def create(**params):
            calls.append(params)
            return FakeStream([NS(type='response.failed', response=NS(error=NS(code='server_error')))])
        with patch('video2pack.time.sleep'):
            with self.assertRaisesRegex(RuntimeError, 'Attempt: 2/2'):
                _request_scene_text(NS(responses=NS(create=create)), {'stream':True}, 'm', 1000)
        self.assertEqual(len(calls), 2)

    def test_streamed_json_inference_uses_siwc_compliant_fields(self):
        scene={'name':'one','size':[2,2,2],'ops':[{'block':'minecraft:stone','from':[0,0,0],'to':[1,0,1]}]}
        data=json.dumps(scene)
        events=[NS(type='response.output_text.delta',delta=data[:25]),
                NS(type='response.output_text.delta',delta=data[25:]),
                NS(type='response.completed', response=NS(output=[]))]
        seen={}
        class DummyOpenAI:
            def __init__(self,**params):
                seen['init']=params
                self.responses=self
            def create(self, **params):
                seen['request']=params
                return FakeStream(events)
        with patch('video2pack.video_frames',return_value=[(1,b'jpeg'),(2,b'jpeg')]), \
             patch.dict(sys.modules, {'openai': NS(OpenAI=DummyOpenAI)}):
            actual=infer_scene(Path('mock.mp4'), 'account-listed-model', 2,
                               auth_mode='chatgpt',access_token='fake-token')
        self.assertEqual(actual['size'],[2,2,2])
        self.assertEqual(seen['init']['api_key'],'fake-token')
        self.assertEqual(seen['request']['store'],False)
        self.assertEqual(seen['request']['stream'],True)
        self.assertNotIn('max_output_tokens',seen['request'])
        self.assertEqual(seen['request']['model'],'account-listed-model')
        self.assertEqual(seen['request']['text']['format']['type'],'json_schema')
        self.assertEqual(len(seen['request']['input'][0]['content']), 5)

    def test_video_to_zip_with_fake_model_stream(self):
        import tempfile
        import zipfile
        scene={'name':'tiny','size':[2,2,2],
               'ops':[{'block':'minecraft:stone','from':[0,0,0],'to':[1,0,1]}]}
        payload=json.dumps(scene)
        class DummyOpenAI:
            def __init__(self, **params): self.responses=self
            def create(self, **params):
                return FakeStream([NS(type='response.output_text.delta',delta=payload),
                                   NS(type='response.completed',response=NS(output=[]))])
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'video_build.zip'
            with patch('video2pack.video_frames',return_value=[(1,b'jpeg')]), \
                 patch.dict(sys.modules, {'openai': NS(OpenAI=DummyOpenAI)}):
                summary=run(video='test.mp4',out=str(out), model='mock', frames=2,
                            auth_mode='chatgpt',access_token='test')
            self.assertEqual(summary['commands'],1)
            self.assertTrue(out.is_file())
            self.assertEqual(list(Path(temp).iterdir()), [out])
            self.assertNotIn('scene_json', summary)
            with zipfile.ZipFile(out) as zipfile_obj:
                self.assertIn('data/vidbuild/function/build.mcfunction',zipfile_obj.namelist())
                self.assertIn('data/vidbuild/function/undo.mcfunction',zipfile_obj.namelist())

    def test_incomplete_stream_is_rejected(self):
        class DummyOpenAI:
            def __init__(self, **params): self.responses=self
            def create(self, **params): return FakeStream([NS(type='response.output_text.delta',delta='{}')])
        with patch('video2pack.video_frames',return_value=[(1,b'jpeg')]), patch.dict(sys.modules, {'openai': NS(OpenAI=DummyOpenAI)}):
            with self.assertRaisesRegex(RuntimeError, 'without response.completed'):
                infer_scene(Path('mock.mp4'),'m', 2, auth_mode='chatgpt', access_token='test')

if __name__=='__main__':
    unittest.main()
