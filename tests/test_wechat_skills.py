from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_sidecar.mcp import tools_for
from agent_sidecar.service import DASHBOARD_UI, Sidecar
from agent_sidecar.store import LocalStore
from agent_sidecar.wechat_skills import CATALOG_SCHEMA, CATALOG_VERSION, wechat_catalog_payload, wechat_skill_catalog


class WeChatSkillCatalogTests(unittest.TestCase):
    def test_catalog_covers_every_product_map_module_with_independent_manifests(self):
        skills = wechat_skill_catalog()
        self.assertGreaterEqual(len(skills), 350)
        self.assertEqual(len(skills), len({skill["skill_id"] for skill in skills}))
        self.assertEqual(
            {skill["category"] for skill in skills},
            {
                "账号与身份",
                "单聊与消息",
                "联系人与好友",
                "群聊与群管理",
                "朋友圈",
                "发现与内容生态",
                "视频号与直播",
                "公众号",
                "小程序与小游戏",
                "微信支付与生活服务",
                "个人工具与资产",
                "设置、隐私与安全",
                "桌面端与跨设备",
            },
        )
        required = {
            "skill_id",
            "name",
            "version",
            "description",
            "category",
            "feature_name",
            "module",
            "operation_entry",
            "actions",
            "roles",
            "prerequisites",
            "visibility_permissions",
            "states_results",
            "limitations_exceptions",
            "platforms",
            "verification_status",
            "input_schema",
            "execution",
            "source",
        }
        for skill in skills:
            self.assertTrue(required.issubset(skill), skill["skill_id"])
            self.assertEqual(skill["version"], CATALOG_VERSION)
            self.assertEqual(skill["catalog_schema"], CATALOG_SCHEMA)
            self.assertEqual(skill["execution"], "disabled-by-default")
            self.assertEqual(skill["input_schema"]["properties"]["action"]["enum"], skill["actions"])
            self.assertLessEqual(len(skill["skill_id"]), 96)

    def test_representative_features_and_known_product_boundaries_are_present(self):
        by_id = {skill["skill_id"]: skill for skill in wechat_skill_catalog()}
        expected = {
            "wechat.group.create",
            "wechat.moments.post.photo",
            "wechat.contact.add.wechat_id",
            "wechat.group.mute_capability_check",
            "wechat.message.file_transfer_assistant",
            "wechat.channels.publish_video",
            "wechat.live.manage",
            "wechat.official.web_auth",
            "wechat.mini.payment",
            "wechat.pay.payment_code",
            "wechat.privacy.data_export",
            "wechat.desktop.moments_post",
        }
        self.assertTrue(expected.issubset(by_id))
        mute = by_id["wechat.group.mute_capability_check"]
        self.assertIn("不支持", mute["verification_status"])
        self.assertIn("企业微信", " ".join(mute["limitations_exceptions"]))
        self.assertIn("QQ", " ".join(mute["limitations_exceptions"]))

    def test_catalog_payload_marks_each_installed_skill_independently(self):
        payload = wechat_catalog_payload(["wechat.group.create", "wechat.moments.post.photo"])
        self.assertEqual(payload["schema"], CATALOG_SCHEMA)
        self.assertEqual(payload["count"], len(wechat_skill_catalog()))
        self.assertEqual(payload["installed_count"], 2)
        installed = {skill["skill_id"] for skill in payload["skills"] if skill["installed"]}
        self.assertEqual(installed, {"wechat.group.create", "wechat.moments.post.photo"})
        self.assertEqual(sum(category["count"] for category in payload["categories"]), payload["count"])

    def test_sidecar_installs_selected_or_full_catalog_without_enabling_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            sidecar = Sidecar.__new__(Sidecar)
            sidecar.store = LocalStore(Path(temporary) / "state.db")
            sidecar.identity = SimpleNamespace(agent_id="agent:test")
            result = sidecar.install_wechat_skills(["wechat.group.create", "wechat.moments.post.photo"])
            self.assertEqual(result["installed_count"], 2)
            installed = sidecar.store.skills()
            self.assertEqual({item["skill_id"] for item in installed}, {"wechat.group.create", "wechat.moments.post.photo"})
            self.assertTrue(all(item["execution"] == "disabled-by-default" for item in installed))
            self.assertTrue((Path(temporary) / "skills" / "wechat.group.create.json").exists())
            with self.assertRaisesRegex(ValueError, "unknown WeChat skill IDs"):
                sidecar.install_wechat_skills(["wechat.not-real"])
            full = sidecar.install_wechat_skills()
            self.assertEqual(full["installed_count"], len(wechat_skill_catalog()))
            self.assertEqual(len(sidecar.store.skills()), len(wechat_skill_catalog()))
            self.assertTrue((Path(temporary) / "skills" / "wechat.desktop.mobile_only.json").exists())
            sidecar.store.close()

    def test_mcp_and_dashboard_expose_catalog_management(self):
        with tempfile.TemporaryDirectory() as temporary:
            sidecar = Sidecar.__new__(Sidecar)
            sidecar.store = LocalStore(Path(temporary) / "state.db")
            names = {tool["name"] for tool in tools_for(sidecar)}
            self.assertIn("skill.catalog.list", names)
            self.assertIn("skill.catalog.install", names)
            sidecar.store.close()
        self.assertIn("微信全功能 Skill 目录", DASHBOARD_UI)
        self.assertIn("/v1/skills/catalog", DASHBOARD_UI)
        self.assertIn("install_wechat_all", DASHBOARD_UI)
        self.assertIn("群聊、朋友圈、支付", DASHBOARD_UI)


if __name__ == "__main__":
    unittest.main()
