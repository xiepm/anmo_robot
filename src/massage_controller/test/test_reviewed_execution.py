import copy
import threading
import unittest

from massage_controller.noncontact_plan import reviewed_execution
import test_route_review as review_tests


class ReviewedExecutionTests(unittest.TestCase):
    def bundle(self):
        d=review_tests.inputs();d['profile'].update(path_confirmed=True,virtual_model_check='DEFERRED')
        helper=review_tests.ReviewTests()
        def query(request):
            if request['kind']=='ik' and request['pose'][0]>-599 and request['pose'][2]>400:
                raise ValueError('higher route unreachable')
            return helper.query(request)
        return helper.reviewer(query).run(d,threading.Event())

    def execute(self,b,profile=None,points=None,revision=None,actual=None):
        from massage_controller.waypoint_plan import pose
        return reviewed_execution(b,profile or b['details']['profile'],
            points or b['inputs']['confirmation']['points'],revision or b['summary']['revision'],
            actual or pose(b['details']['candidate']['start_pose'],'start'))

    def test_exact_lower_candidate_is_used_instead_of_initial_height(self):
        b=self.bundle();self.assertTrue(b['summary']['kinematics_checked'])
        self.assertEqual(b['summary']['transfer_height_mm'],290)
        plan,targets=self.execute(b)
        self.assertEqual(plan['points'],b['details']['candidate']['points'])
        self.assertEqual(plan['start_pose'],b['details']['candidate']['start_pose'])
        self.assertEqual(targets,b['summary']['targets'])

    def test_site_approval_revision_profile_points_and_origin_are_enforced(self):
        b=self.bundle()
        for args in [dict(profile=dict(b['details']['profile'],path_confirmed=False)),
                     dict(profile=dict(b['details']['profile'],speed_mm_s=6)),
                     dict(revision='different'),dict(actual=[-590,-200,500,180,0,0])]:
            with self.subTest(args=args),self.assertRaises(ValueError):self.execute(b,**args)
        points=copy.deepcopy(b['inputs']['confirmation']['points']);points[0]['base_point_mm'][0]+=1
        with self.assertRaises(ValueError): self.execute(b,points=points)

    def test_candidate_tampering_and_failed_ik_cannot_be_promoted(self):
        b=self.bundle();b['details']['candidate']['points'][0]['pose']['z_mm']+=5
        with self.assertRaises(ValueError):self.execute(b)
        b=self.bundle();b['summary']['kinematics_checked']=False
        with self.assertRaises(ValueError):self.execute(b)


if __name__=='__main__':unittest.main()
