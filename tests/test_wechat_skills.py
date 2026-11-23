"""Legacy import paths remain compatible, but never restore the WeChat catalog."""
import unittest

from agent_sidecar.communication_skills import communication_catalog_payload, communication_skill_catalog
from agent_sidecar.wechat_skills import wechat_catalog_payload, wechat_skill_by_id, wechat_skill_catalog


class LegacyCatalogCompatibilityTests(unittest.TestCase):
    def test_old_catalog_import_returns_only_current_communication_tools(self):
        self.assertEqual(wechat_skill_catalog(), communication_skill_catalog())
        self.assertTrue(all(s['skill_id'].startswith('sidecar.') for s in wechat_skill_catalog()))

    def test_old_payload_import_tracks_current_installations(self):
        installed = ['sidecar.actor.register', 'sidecar.project.create']
        self.assertEqual(wechat_catalog_payload(installed), communication_catalog_payload(installed))
        self.assertEqual(wechat_catalog_payload(installed)['installed_count'], 2)

    def test_retired_wechat_ids_are_no_longer_installable(self):
        self.assertIsNone(wechat_skill_by_id('wechat.pay.payment_code'))
        self.assertIsNone(wechat_skill_by_id('wechat.moments.post.photo'))
        self.assertEqual(wechat_skill_by_id('sidecar.actor.send')['execution'], 'native-sidecar')
