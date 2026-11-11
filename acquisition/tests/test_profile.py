import errno
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from acquisition import profile as module
from acquisition.profile import Profile, RecoveryRequired, ProfileError, Busy, manifest, atomic_json

@unittest.skipUnless(os.name == 'posix', 'Linux flock and directory fsync required')
class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.owner = Profile(self.tmp.name).__enter__()
        self.addCleanup(self.owner.__exit__)
        self.owner.initialize(confirm_new=True)
        self.source = self.owner.path(self.owner.read_state()['active'])
        (self.source / 'cookie-fixture').write_bytes(b'original-cookie')

    def validator(self, path):
        self.owner.allow_browser(path)
        self.assertEqual((path / 'cookie-fixture').read_bytes(), b'original-cookie')

    def recover(self, validator=None):
        return self.owner.recover(validator or self.validator, confirm_stopped=True, confirm_external=True)

    def unchanged(self, before):
        self.assertEqual(manifest(self.source), before)
        for path in (self.owner.root / 'backups').glob('*.json'):
            record = json.loads(path.read_text())
            self.assertEqual(manifest(path.with_suffix('')), record['files'])

    def test_reinitialize_rejected(self):
        with self.assertRaises(RecoveryRequired): self.owner.initialize(confirm_new=True)

    def test_normal_session_and_verify(self):
        with self.owner.session() as path: self.owner.allow_browser(path)
        self.assertEqual(self.owner.verify()['status'], 'ready')

    def test_error_leaves_dirty_even_without_singleton(self):
        with self.assertRaises(ValueError):
            with self.owner.session(): raise ValueError('failure')
        with self.assertRaises(RecoveryRequired): self.owner.verify()

    def test_no_confirmations(self):
        for first,second in [(False,False),(True,False),(False,True)]:
            with self.assertRaises(ProfileError): self.owner.recover(self.validator,confirm_stopped=first,confirm_external=second)
        self.assertFalse(self.owner.operation_path.exists())

    def test_busy(self):
        with self.assertRaises(Busy):
            with Profile(self.tmp.name): pass

    def test_alias_uses_same_lock(self):
        with self.assertRaises(Busy):
            with Profile(Path(self.tmp.name)/'.'): pass

    def test_recover_copies_does_not_touch_source(self):
        (self.source/'SingletonLock').symlink_to('not-a-host-proof-123')
        before=manifest(self.source)
        result=self.recover()
        self.assertNotEqual(result['generation'],self.source.name)
        self.unchanged(before)
        self.assertTrue((self.source/'SingletonLock').is_symlink())

    def test_unexpected_singleton_type(self):
        (self.source/'SingletonLock').write_text('unexpected')
        before=manifest(self.source)
        with self.assertRaises(RecoveryRequired): self.recover()
        self.unchanged(before)

    def test_copy_failure(self):
        before=manifest(self.source)
        with patch.object(module.shutil,'copytree',side_effect=OSError(errno.EIO,'copy')):
            with self.assertRaises(OSError): self.recover()
        self.unchanged(before)
        with self.assertRaises(RecoveryRequired): self.owner.verify()

    def test_preflight_disk_full(self):
        before=manifest(self.source)
        with patch.object(module.shutil,'disk_usage',return_value=shutil._ntuple_diskusage(100,100,0)):
            with self.assertRaises(OSError) as error: self.recover()
        self.assertEqual(error.exception.errno,errno.ENOSPC)
        self.unchanged(before)

    def test_copy_disk_full(self):
        before=manifest(self.source)
        with patch.object(module.shutil,'copytree',side_effect=OSError(errno.ENOSPC,'copy')):
            with self.assertRaises(OSError): self.recover()
        self.unchanged(before)

    def test_candidate_copy_failure_preserves_backup(self):
        before=manifest(self.source); original=module.shutil.copytree
        def copy(source,target,**kw):
            if Path(target).parent.name=='generations': raise OSError(errno.EIO,'candidate')
            return original(source,target,**kw)
        with patch.object(module.shutil,'copytree',side_effect=copy):
            with self.assertRaises(OSError): self.recover()
        self.unchanged(before)

    def test_validation_failure(self):
        before=manifest(self.source)
        def fail(path): raise RuntimeError('validation')
        with self.assertRaises(RuntimeError): self.recover(fail)
        self.unchanged(before)
        with self.assertRaises(RecoveryRequired): self.owner.verify()

    def test_pointer_failure_requires_manual_recovery(self):
        before=manifest(self.source); original=module.atomic_json
        def write(path,value):
            if path==self.owner.state_path: raise OSError(errno.ENOSPC,'pointer')
            return original(path,value)
        with patch.object(module,'atomic_json',side_effect=write):
            with self.assertRaises(OSError): self.recover()
        self.unchanged(before)
        with self.assertRaises(RecoveryRequired): self.owner.verify()

    def test_after_pointer_failure_keeps_maintenance_guard(self):
        before=manifest(self.source); original=module.atomic_json
        def write(path,value):
            original(path,value)
            if path==self.owner.state_path: raise OSError(errno.EIO,'after pointer')
        with patch.object(module,'atomic_json',side_effect=write):
            with self.assertRaises(OSError): self.recover()
        self.assertNotEqual(self.owner.read_state()['active'],self.source.name)
        with self.assertRaises(RecoveryRequired): self.owner.verify()
        self.unchanged(before)
        self.recover()
        self.assertEqual(self.owner.verify()['status'],'ready')

    def test_missing_active_not_recreated(self):
        shutil.rmtree(self.source)
        with self.assertRaises(RecoveryRequired): self.owner.verify()

    def test_no_escape_link(self):
        (self.source/'escape').symlink_to('/etc/passwd')
        with self.assertRaises(RecoveryRequired): self.recover()

    def test_no_automatic_recovery(self):
        atomic_json(self.owner.operation_path,{'interrupted':True})
        with self.assertRaises(RecoveryRequired):
            with self.owner.session(): pass

    def test_lock_released_if_directory_sync_fails(self):
        self.owner.__exit__()
        with patch.object(module,'sync_dir',side_effect=OSError(errno.EIO,'sync')):
            with self.assertRaises(OSError): Profile(self.tmp.name).__enter__()
        self.owner.__enter__()

    def test_snapshot_sync_failure_preserves_source_and_copy(self):
        before=manifest(self.source)
        with patch.object(module,'sync_tree',side_effect=OSError(errno.EIO,'backup fsync')):
            with self.assertRaises(OSError): self.recover()
        self.assertEqual(manifest(self.source),before)
        self.assertTrue(self.owner.operation_path.exists())

    def test_wrong_backup_detected_before_candidate(self):
        original=module.shutil.copytree;before=manifest(self.source)
        def corrupt(source,target,**kw):
            result=original(source,target,**kw)
            (Path(target)/'cookie-fixture').write_bytes(b'bad copy')
            return result
        with patch.object(module.shutil,'copytree',side_effect=corrupt):
            with self.assertRaises(RecoveryRequired): self.recover()
        self.assertEqual(manifest(self.source),before)
        self.assertEqual(len(list((self.owner.root/'generations').iterdir())),1)

    def test_linux_symlink_alias_uses_same_lock(self):
        alias=Path(self.tmp.name).parent/('profile-alias-'+self.source.name)
        alias.symlink_to(self.owner.root,target_is_directory=True)
        self.addCleanup(alias.unlink)
        with self.assertRaises(Busy):
            with Profile(alias): pass

    def test_session_cannot_open_before_durable_mark(self):
        with patch.object(module,'atomic_json',side_effect=OSError(errno.ENOSPC,'session mark')):
            with self.assertRaises(OSError):
                with self.owner.session(): self.fail('Session must not start')

    def test_unmanaged_nonempty_target_not_initialized(self):
        with tempfile.TemporaryDirectory() as other:
            (Path(other)/'important').write_bytes(b'preserve')
            with Profile(other) as owner:
                with self.assertRaises(RecoveryRequired): owner.initialize(confirm_new=True)
            self.assertEqual((Path(other)/'important').read_bytes(),b'preserve')

    def test_parent_directory_sync_failure_never_publishes(self):
        before=manifest(self.source);original=module.sync_dir
        def fail(path):
            if Path(path)==self.owner.root/'generations': raise OSError(errno.EIO,'generation directory fsync')
            return original(path)
        with patch.object(module,'sync_dir',side_effect=fail):
            with self.assertRaises(OSError): self.recover()
        self.assertEqual(self.owner.read_state()['active'],self.source.name)
        with self.assertRaises(RecoveryRequired): self.owner.verify()
        self.unchanged(before)

    def test_malformed_session_never_treated_as_clean(self):
        state=self.owner.read_state();state['session']=''
        atomic_json(self.owner.state_path,state)
        with self.assertRaises(RecoveryRequired): self.owner.verify()
