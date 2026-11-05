# Continuous Goal Execution

本文件是仓库中的持续工作入口。完整目标、阶段路线、接口契约和最终验收清单见：

- `联邦学习持续工作目标文档.md`
- `docs/FL_SPEC.md`
- `docs/BENCHMARK_SPEC.md`
- `docs/HARNESS_SPEC.md`

## 当前状态

- 阶段 0：已完成基础依赖和现有 Sidecar 回归验证。
- 阶段 1：已完成 Toy Harness、Coordinator、FederatedNode 和聚合核心。
- 阶段 2：已完成显式信任 allowlist、pending/trusted/revoked 状态、Web UI 和节点登录能力广播。
- 阶段 3：已完成 Sidecar 加密 FL round 的跨进程运行时接口，并提供网络 demo 和独立 OS 进程 demo。
- 阶段 4：已完成 FedAvg、FedProx、Secure FedAvg、Median、Trimmed Mean、Krum 及投毒模拟。
- 阶段 5：已完成 Star、Hierarchical、Gossip 结构对比，以及安全性、通信、延迟 Benchmark 和 JSON/CSV/JSONL/Markdown 输出。
- 阶段 6：已完成 Python、Command、HTTP、现有 DSH Controller、结构化 DSH training bridge 和 DeepSeek 配置型 Harness 适配层，并提供 `harness describe`。
- 阶段 7：进行中，仍需根据真实 DeepSeek Harness 的外部入口完成实际模型映射，并继续加强生产部署安全、artifact 大模型传输、真实 DeepSeek 映射、Secure FedAvg dropout recovery、Coordinator HA 和生产级隐私 accountant。
- 阶段 8：进行中，目标见 `docs/COLLABORATION_SPEC.md`。节点能力广播、签名单向信任通知/双向确认、签名项目邀请接受流程、历史成员清单证明、确定性 Merkle root、加密 bucket-range 双向差异同步、可配置且健康感知的部分 Gossip 邻居、持久化衰减评分、失败 cooldown、gossip TTL/去重、成员 epoch 内容密钥轮换、独立进程 Owner 故障/恢复/分区收敛、HTTPS 指纹 pinning、file/keychain/external helper 密钥存储和 backend preflight MVP 已完成；仍需完成真实设备密钥库集成和真实跨机器 NAT/防火墙验收。

## 每次继续工作

1. 先运行：

   ```bash
   python -m unittest discover -s tests -v
   ```

2. 再运行最小流程：

   ```bash
   python scripts/run_federated_demo.py
   python scripts/run_federated_network_demo.py
   ```

3. 选择当前阶段中最靠近完整交付的未完成任务，直接实现，不只输出计划。
4. 修改协议时同时补充发送端、接收端和攻击/失败测试。
5. 结束前记录测试命令、结果、输出目录、阻塞和下一项唯一最重要任务。

## 当前下一项优先任务

实现生产密钥存储适配（Keychain/TPM/HSM）和真实跨机器 NAT/防火墙部署验收。
