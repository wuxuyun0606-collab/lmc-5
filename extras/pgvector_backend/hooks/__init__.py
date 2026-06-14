"""Claude Code hook 参考实现 —— 把 LMC-5 接进 agent runtime 的"管道层"。

模块：
  session_start         开机注入 startup pack（identity / facts / narrative / threads / perception）
  user_prompt_submit    每轮用户消息触发多通道召回 + 情绪坐标
  session_end           关窗口归档 raw events，可选触发 express dream

约定：
  - 所有 hook 通过 stdin 接 JSON event，通过 stdout 输出 additionalContext
  - 异常一律 log + 退出码 0，不阻塞主流程（让 agent 继续工作比 hook 完美更重要）
  - 环境变量：LMC5_PG_DSN（必填）/ LMC5_PERCEPTION_CACHE / LMC5_EXPRESS_DREAM

settings.json 接线示例见各模块的 docstring。
"""
