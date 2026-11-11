"""Size and integrity policy for Spanish comments evidence in Group 2 bundles."""
import unittest

from acquisition.recipe import comments_capacity_for_next, comments_file_bytes, pending_comments
from persistencia.bundle import (
    MAX_COMMENTS_FILE, MAX_FILE, MAX_LINE, MAX_TOTAL, MAX_WIRE, Rejected, _verify_group_file, encode, sha,
)


class GroupCommentsBundleLimits(unittest.TestCase):
    def test_5_34_mb_comments_file_is_accepted_with_valid_hashes_and_jsonl(self):
        rows = [dict(event_id=16600011 + n, body='x' * (133422 if n < 10 else 133423)) for n in range(40)]
        raw = b''.join(encode(row) + b'\n' for row in rows)
        self.assertEqual(len(raw), 5_338_190)
        descriptor = dict(bytes=len(raw), sha256=sha(raw))

        verified, total = _verify_group_file('comments.jsonl', descriptor, raw, 0, with_comments=True)

        self.assertEqual(verified, rows)
        self.assertEqual(total, len(raw))

    def test_comments_above_8_mib_are_rejected(self):
        raw = b'{"body":"' + b'x' * MAX_COMMENTS_FILE + b'"}\n'
        with self.assertRaisesRegex(Rejected, 'Invalid file size/hash'):
            _verify_group_file('comments.jsonl', dict(bytes=len(raw), sha256=sha(raw)), raw, 0, with_comments=True)

    def test_other_jsonl_files_keep_the_4_mib_file_cap(self):
        raw = b'{"body":"' + b'x' * MAX_FILE + b'"}\n'
        with self.assertRaisesRegex(Rejected, 'Invalid file size/hash'):
            _verify_group_file('calendar.jsonl', dict(bytes=len(raw), sha256=sha(raw)), raw, 0, with_comments=True)

    def test_aggregate_and_wire_limits_are_unchanged(self):
        self.assertEqual(MAX_TOTAL, 64 * 1024 * 1024)
        self.assertEqual(MAX_WIRE, 96 * 1024 * 1024)
        self.assertEqual(MAX_LINE, 2 * 1024 * 1024)
        raw = b'{"x":1}\n'
        with self.assertRaisesRegex(Rejected, 'Invalid file size/hash'):
            _verify_group_file('comments.jsonl', dict(bytes=len(raw), sha256=sha(raw)), raw,
                               MAX_TOTAL - len(raw) + 1, with_comments=True)

    def test_wrong_sha256_is_still_rejected(self):
        raw = b'{"x":1}\n'
        with self.assertRaisesRegex(Rejected, 'Invalid file size/hash'):
            _verify_group_file('comments.jsonl', dict(bytes=len(raw), sha256='0' * 64), raw, 0, with_comments=True)

    def test_invalid_jsonl_is_still_rejected(self):
        raw = b'{invalid}\n'
        with self.assertRaises(Rejected):
            _verify_group_file('comments.jsonl', dict(bytes=len(raw), sha256=sha(raw)), raw, 0, with_comments=True)

    def test_lines_over_2_mib_are_still_rejected(self):
        raw = encode({'body': 'x' * MAX_LINE}) + b'\n'
        with self.assertRaisesRegex(Rejected, 'Invalid JSONL'):
            _verify_group_file('comments.jsonl', dict(bytes=len(raw), sha256=sha(raw)), raw, 0, with_comments=True)

    def test_capacity_planner_defers_without_charging_attempts(self):
        self.assertTrue(comments_capacity_for_next([]))
        # Five valid sub-limit records can be encoded, but the planner will not start another request
        # unless a whole MAX_LINE record and its newline could still fit.
        evidence = [dict(body='x' * 1_300_000) for _ in range(5)]
        self.assertLessEqual(comments_file_bytes(evidence), MAX_COMMENTS_FILE)
        self.assertFalse(comments_capacity_for_next(evidence))

    def test_capacity_deferral_leaves_unattempted_comments_pending(self):
        candidates = set(range(40))
        attempted = {0, 1, 2}
        self.assertFalse(comments_capacity_for_next([dict(body='x' * 1_300_000) for _ in range(5)]))
        self.assertEqual(pending_comments(candidates, attempted), list(range(3, 40)))


if __name__ == '__main__':
    unittest.main()
