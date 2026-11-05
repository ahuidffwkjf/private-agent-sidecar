"""Export the existing catalog as independent, agent-loadable Skill folders."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent_sidecar.wechat_skills import wechat_skill_catalog


def export_skills(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    index = []
    for item in wechat_skill_catalog():
        name = item['skill_id'].replace('.', '-').replace('_', '-')
        if not re.fullmatch(r'[a-z0-9-]{1,64}', name):
            raise ValueError('invalid generated Skill name')
        directory = destination / name
        directory.mkdir(exist_ok=True)
        (directory / 'references').mkdir(exist_ok=True)
        manifest = directory / 'references' / 'manifest.json'
        manifest.write_text(json.dumps(item, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        description = f"{item['name']}：{item['description']} 用于理解该功能或规划 Sidecar 中的对应操作；不是已实现的微信自动化执行器。"
        text = f'''---
name: {name}
description: {json.dumps(description, ensure_ascii=False)}
---

# {item['name']}

## 使用边界

这是此前功能目录的独立 Skill，不是 Plugin。它提供操作语义和检查步骤，
不自带真实微信执行器，也不代表 Sidecar 已实现该功能的全部动作。
原始目录版本：{item['version']}；模块：{item['category']}。
只有经过核实的现有 API/工具才能执行动作，不能根据目录猜测接口。

## 工作流程

1. 先区分用户要操作 Sidecar 的类微信功能，还是实际腾讯微信账号；两者不能替代。
2. 阅读 [manifest.json](references/manifest.json)，确认 action、对象、内容、角色和权限。
   本功能动作范围：{', '.join(item['actions'])}。
3. 检查当前安装版本和已授权工具的真实能力。若是 Sidecar，查其随包文档和 API；
   若是实际微信，必须有明确授权且可用的客户端执行工具。没有执行器则返回
   `not_implemented`，列出缺少条件，不创建虚假的成功结果。
4. 对外发送、发布、删除、好友授权、资金和账号安全操作，在必要的具体确认后才执行；
   Skill 加载和安装不是对这些动作的批量授权。支付、验证码、生物验证交由用户处理。
5. 执行后通过返回结果或界面状态验证；不把“已提交”“进程启动”当作“已完成”。
   未确认成功的动作不要盲目重试，先查询结果以免重复发送或支付。

## 资料与核验

原始参考入口：{item['operation_entry']}。
适用动作、前置条件和限制在 manifest 中。其历史 `verification_status` 是旧目录元数据，
不是本包提供的逐项客户端实测或官方证据；版本敏感规则需重新核验，不写死人数、时限、限额。
`execution: disabled-by-default` 保持不变；禁止通过绕过 runner 策略来执行。
'''
        (directory / 'SKILL.md').write_text(text, encoding='utf-8')
        index.append({'skill_id': item['skill_id'], 'name': name, 'category': item['category'],
                      'path': name + '/SKILL.md', 'execution': 'disabled-by-default',
                      'implementation': 'catalog-guidance-not-executor'})
    (destination / 'index.json').write_text(json.dumps(index, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return index


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(json.dumps({'exported': len(export_skills(args.output))}))
