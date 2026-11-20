# AML 历史报名说明

以下保留 2026 年 10 月 16 日报名表的措辞。代码 Full 的后续对账记录及文本复测的公开状态见[版本对照表](README.md#version-and-source-correspondence)。

系统名称：LMC-5（五维活体记忆坐标系统）

版本名称：`v0.3.0-aml.1`

公开仓库：<https://github.com/wuxuyun0606-collab/lmc-5>

报名快照源码：<https://github.com/wuxuyun0606-collab/lmc-5/tree/v0.3.0-aml.1/extras/aml>

Add、Search 与健康检查地址填写实际考场部署地址；认证选择
`Authorization: Bearer`，Key 填记忆服务访问密钥，不填 JEV 或 embedding 密钥。
Add 同步处理，选填的 Add 状态地址留空。

## 提交说明与运行指南（可复制）

LMC-5 为本团队开发的五维记忆系统，采用 X 时间线、Y 关系网、Z 事实演化、E 体验信号、M 记忆代谢的架构。设计参考与致谢见公开仓库 docs/credits.md。

重排层可按需适配 Luna、JEV、Clef，使用者需自行配置对应服务的 API 密钥，项目不提供共享密钥或共享调用额度。当前参赛默认使用 JEV；仓库保留 Clef 参考后端，Luna 需另接适配器。

本次参赛版本为 v0.3.0-aml.1，基于 LMC-5 0.3.0 核心，包含公开的 AML HTTP 接口、JEV 重排桥接层及预算账本。已部署同步 Add、Search 和健康检查接口，地址见表单；Add/Search 使用 Authorization: Bearer 鉴权，服务常驻并配置开机自启。

考场实例使用按用户隔离的 SQLite 存储，支持相同 request_id 与请求内容的顺序重试幂等。检索采用词法与向量召回、RRF 融合及第三方 JEV 重排。向量模型为 DashScope text-embedding-v4；重排服务由 TypeSafe 提供，模型为 jev-1.13.0。JEV 最多处理 100 个候选，最终返回不超过 min(top_k, 50) 条；召回条数仍受请求 top_k 和可检索内容影响。结果按需附带同会话相邻轮次，并按时间或当前事实查询意图排序。

JEV 单次检索调用并发上限为 16，默认进程级 JEV 调用并发上限为 16，单请求体上限为 8 MB。这里的并发限制指重排调用，不代表已验证的 HTTP 客户端并发容量。JEV 调用受独立预算账本约束，失败时回退到 RRF 结果。

接口源码、重排适配、配置模板、启动与账本初始化步骤、离线联测见 extras/aml/README.md；该参赛快照对应标签 v0.3.0-aml.1。公开代码不包含真实密钥、记忆数据或评测数据集。JEV 为第三方服务，本项目不声明其模型为自研。基础开源核心的全部可选能力不等于本次考场全部启用能力；多跳实验与拒答阈值默认关闭。
