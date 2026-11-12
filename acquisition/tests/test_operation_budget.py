"""The manual-operation send ledger is shared across Acquisition batches and survives process reconstruction."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
import uuid

from acquisition.operation_budget import OperationBudget, OperationBudgetError


class OperationBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='rj-operation-budget-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.operation=str(uuid.uuid4())

    def test_one_allowance_is_shared_between_batches_and_reconstructed_instances(self):
        first=OperationBudget(self.root,self.operation,50)
        for index in range(31):
            self.assertTrue(first.reserve_send(job_id='batch-1',channel='curl',path=f'/event/{index}')['allowed'])
        second=OperationBudget(self.root,self.operation,50)
        for index in range(19):
            self.assertTrue(second.reserve_send(job_id='batch-2',channel='bridge',path=f'/event/{index}')['allowed'])
        self.assertFalse(OperationBudget(self.root,self.operation,50).reserve_send(
            job_id='after-restart',channel='bridge',path='/event/extra')['allowed'])
        state=second.snapshot()
        self.assertEqual((state['used'],state['remaining'],state['maximum']),(50,0,50))

    def test_concurrent_batches_cannot_reserve_more_than_the_limit(self):
        budget=OperationBudget(self.root,self.operation,50)
        def reserve(index):
            return budget.reserve_send(job_id=f'batch-{index%3}',channel='browser-page',path='/captcha.html')['allowed']
        with ThreadPoolExecutor(max_workers=12) as pool:
            accepted=list(pool.map(reserve,range(100)))
        self.assertEqual(sum(accepted),50)
        self.assertEqual(OperationBudget(self.root,self.operation,50).snapshot()['used'],50)

    def test_history_recovery_slots_are_unique_and_survive_restart(self):
        budget=OperationBudget(self.root,self.operation,50)
        self.assertTrue(budget.reserve_history(4001,3))
        self.assertTrue(OperationBudget(self.root,self.operation,50).reserve_history(4001,3))
        self.assertTrue(budget.reserve_history(4002,3))
        self.assertTrue(budget.reserve_history(4003,3))
        self.assertFalse(budget.reserve_history(4004,3))
        self.assertEqual(budget.snapshot()['history_matches'],[4001,4002,4003])

    def test_existing_operation_cannot_raise_its_limit_or_reset_corrupt_state(self):
        budget=OperationBudget(self.root,self.operation,10)
        budget.reserve_send(job_id='one',channel='curl',path='/event/1')
        with self.assertRaises(OperationBudgetError):
            OperationBudget(self.root,self.operation,50).snapshot()
        budget.path.write_text('{broken')
        with self.assertRaises(OperationBudgetError):
            budget.snapshot()


if __name__=='__main__':
    unittest.main(verbosity=2)
