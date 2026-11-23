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
        description = f"{item['name']}：{item['description']} 通过本机 Sidecar 原生通信工具执行。"
        text = f'''---
name: {name}
description: {json.dumps(description, ensure_ascii=False)}
---

# {item['name']}

## 使用边界

这是 Sidecar 原生通信 Skill，不操作腾讯微信账号，不自带模型运行时。
目录版本：{item['version']}；模块：{item['category']}。
节点身份 agent_id 与 Harness 智能体身份 actor_id 分开；智能体不能绕过节点信任。

## 工作流程

1. 阅读 [manifest.json](references/manifest.json)，核对 input_schema、对象与权限。
2. 通过 POST /v1/skills/catalog/install 安装此 skill_id；安装不等于对写操作的授权。
3. 在受信任的本机 Sidecar 上调用 POST /mcp，method 为 tools/call，
   params.name 为 skill.{item['skill_id']}，params.arguments 严格匹配 input_schema。
4. {'此操作会改变状态或发送消息，执行前确认目标和内容。' if item['mutates'] else '此操作查询当前状态。'}
5. 检查 HTTP 错误、JSON-RPC error 和 result.isError；成功时 result.content[0].json 是结构化结果。
   目标不可达或权限不足时如实报告；不把安装、广播发现或提交当作成功通信。

## 资料与核验

入口：{item['operation_entry']}。
`execution: native-sidecar` 只映射代码中固定的通信接口，不运行任意命令或 URL。
未识别的自定义 Skill 仍返回 `not_implemented` / execution disabled；不得绕过执行策略。
'''
        (directory / 'SKILL.md').write_text(text, encoding='utf-8')
        index.append({'skill_id': item['skill_id'], 'name': name, 'category': item['category'],
                      'path': name + '/SKILL.md', 'execution': item['execution'],
                      'implementation': 'native-sidecar-communication'})
    (destination / 'index.json').write_text(json.dumps(index, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return index


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(json.dumps({'exported': len(export_skills(args.output))}))
