"""Compatibility imports for existing installers; the WeChat catalog is retired."""
from .communication_skills import (
    CATALOG_SCHEMA, CATALOG_VERSION, SOURCE,
    communication_catalog_payload as wechat_catalog_payload,
    communication_skill_by_id as wechat_skill_by_id,
    communication_skill_catalog as wechat_skill_catalog,
)
