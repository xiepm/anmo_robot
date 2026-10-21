"""Progress remains informational and belongs to one confirmed revision."""
import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path

from massage_controller.route_review import Reviewer, ReviewManager
from test_simplified_route import fixture, query


class ProgressTests(unittest.TestCase):
    def test_all_samples_are_checked_and_progress_never_approves_motion(self):
        data=fixture();data['profile']['return_to_start']=True
        updates=[]
        result=Reviewer(query).run(data,threading.Event(),updates.append)
        count=result['summary']['sample_count']
        self.assertGreater(count,0)
        self.assertEqual(updates[0]['sample_count'],0)
        self.assertEqual(updates[-1]['sample_count'],count)
        self.assertEqual(len(result['details']['samples']),count)
        self.assertTrue(all('execution_eligible' not in item for item in updates))
        self.assertFalse(result['summary']['execution_eligible'])
        self.assertGreaterEqual(result['review_elapsed_s'],0)

    def test_clear_revision_while_math_pending_discards_progress_and_result(self):
        entered,release=threading.Event(),threading.Event()
        def slow(request):
            if request['kind']=='ik':
                entered.set();release.wait(2)
            return query(request)
        data=fixture()
        class Confirmation: pass
        c=Confirmation();c.value=data['confirmation'];c.scene=data['scene']
        c.scene_context=data['context'];c.packet=data['capture']
        with tempfile.TemporaryDirectory() as tmp:
            profile=Path(tmp)/'profile.json';profile.write_text(json.dumps(data['profile']))
            manager=ReviewManager(slow,tmp,reviewer=Reviewer(slow))
            try:
                self.assertEqual(manager.sync(c,data['sample'],profile)['state'],'CHECKING')
                self.assertTrue(entered.wait(1))
                pending=manager.sync(c,data['sample'],profile)
                self.assertIn('0 /',pending['reason'])
                self.assertFalse(pending['kinematics_checked'])
                c.value=None
                self.assertIsNone(manager.sync(c,data['sample'],profile))
                release.set()
                self.assertIsNone(manager.result)
            finally:
                release.set();manager.close()
            self.assertIsNone(manager.progress)
            self.assertFalse(list(Path(tmp).glob('000*.json')))


if __name__=='__main__': unittest.main()
