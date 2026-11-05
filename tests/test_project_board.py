import unittest

from agent_sidecar.project_board import event_set_root


class ProjectBoardMerkleTests(unittest.TestCase):
    def test_event_set_root_is_order_independent_and_changes_for_membership(self):
        events_a = [{"event_id": "a"}, {"event_id": "b"}, {"event_id": "c"}]
        events_b = [{"event_id": "c"}, {"event_id": "a"}, {"event_id": "b"}]
        self.assertEqual(event_set_root(events_a), event_set_root(events_b))
        self.assertNotEqual(event_set_root(events_a), event_set_root(events_a + [{"event_id": "d"}]))

    def test_empty_event_set_has_a_stable_root(self):
        self.assertEqual(event_set_root([]), event_set_root([]))
        self.assertTrue(event_set_root([]).startswith("sha256:"))
