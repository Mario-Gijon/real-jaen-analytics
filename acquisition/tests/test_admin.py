import asyncio
from contextlib import redirect_stdout,redirect_stderr
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,AsyncMock,MagicMock
from acquisition.admin import main
from acquisition.profile import Profile,RecoveryRequired
from acquisition.browser import managed_browser

@unittest.skipUnless(os.name=='posix','Linux administrative commands')
class AdminTests(unittest.TestCase):
    def test_missing_confirmations_dont_touch_profile(self):
        with tempfile.TemporaryDirectory() as root,redirect_stderr(io.StringIO()):
            self.assertEqual(main(['--root',root,'recover-profile']),74)
            self.assertEqual(list(Path(root).iterdir()),[])

    def test_reject_network_before_touching_profile(self):
        with tempfile.TemporaryDirectory() as root,redirect_stderr(io.StringIO()):
            with patch('acquisition.browser.socket.if_nameindex',return_value=[(1,'lo'),(2,'eth0')]):
                self.assertEqual(main(['--root',root,'initialize-profile','--confirm-new-profile']),74)
            self.assertEqual(list(Path(root).iterdir()),[])

    def test_no_json_authorization_option(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as result: main(['recover-profile','--fence','anything.json'])
        self.assertEqual(result.exception.code,2)

    def test_initialize_and_verify(self):
        with tempfile.TemporaryDirectory() as root,redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--root',root,'initialize-profile','--confirm-new-profile']),0)
            self.assertEqual(main(['--root',root,'verify-profile']),0)

    def test_failed_launch_never_uses_secondary_profile(self):
        async def check(owner):
            engine=MagicMock();engine.chromium.launch_persistent_context=AsyncMock(side_effect=RuntimeError('refused profile'))
            engine.stop=AsyncMock();factory=MagicMock();factory.start=AsyncMock(return_value=engine)
            with patch('patchright.async_api.async_playwright',return_value=factory):
                with self.assertRaises(RuntimeError):
                    async with managed_browser(owner): self.fail('No session permitted')
            self.assertEqual(engine.chromium.launch_persistent_context.await_count,1)
            args,kwargs=engine.chromium.launch_persistent_context.call_args
            self.assertEqual(args[0],str(owner.path(owner.read_state()['active'])))
            self.assertTrue(kwargs['chromium_sandbox'])
            self.assertFalse(kwargs['ignore_https_errors'])
            with self.assertRaises(RecoveryRequired): owner.verify()
            self.assertEqual(len(list((owner.root/'generations').iterdir())),1)
        with tempfile.TemporaryDirectory() as root,Profile(root) as owner:
            owner.initialize(confirm_new=True)
            asyncio.run(check(owner))
