"""E 轴 LLM 评分 · provider-agnostic 版

对应 lmc-5 core 的 E 轴：
原本只占了 valence/arousal/tension 三个字段位，没有写"怎么打分"的实现。
这一版补上 LLM scorer 框架，但完全 provider-free：
  - 不绑 DeepSeek / OpenAI / Anthropic 任何一家
  - LLM 调用走 Callable，调用方提供"prompt → JSON 字典"的回调
  - 失败日志按类别记录（http_timeout / parse_fail / schema_fail / range_fail ...）
  - 字段验证、夹值、敏感词降级——全本地做

rubric 是行为评分量规——决定打分维度。这里给一个默认 rubric 引子，
真正落地时调用方要提供自己的（按情绪学派/产品需求自定）。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


REQUIRED_FIELDS = {"valence", "arousal", "tension", "response_tendency", "growth_delta", "confidence"}

from .anti_hallucination import (
    ANTI_HALLUCINATION_HEADER,
    E_AXIS_TASK_REMINDERS,
)

DEFAULT_RUBRIC = ANTI_HALLUCINATION_HEADER + E_AXIS_TASK_REMINDERS + """
你是一个情感打分器。读完一段记忆后，输出严格 JSON，字段如下：

{
  "valence":    [-1.0, 1.0],  // 情感价：负面到正面
  "arousal":    [0.0, 1.0],   // 唤醒度：平静到激动
  "tension":    [0.0, 1.0],   // 张力：松弛到紧绷
  "confidence": [0.0, 1.0],   // 你对这次打分的把握
  "response_tendency": "comfort|engage|withdraw|alert",  // 后续应对姿态
  "growth_delta": "growth|stable|setback"               // 这段记忆对长期的方向
}

规则：
- 范围严格在区间内，超界会被丢弃
- 不要写解释，只输出 JSON
- 不确定就把 confidence 调低；confidence < 0.3 会被闸门丢弃，所以保守拒答优于乱打
"""


@dataclass
class EAxisScore:
    valence: float
    arousal: float
    tension: float
    confidence: float
    response_tendency: str
    growth_delta: str
    scorer: str = ""
    rubric_version: str = ""


class FailReason:
    READ_CONFIG = "read_config"
    HTTP_ERROR = "http_error"
    HTTP_TIMEOUT = "http_timeout"
    EMPTY_RESPONSE = "empty_response"
    NO_JSON_IN_TEXT = "no_json_in_text"
    PARSE_FAIL = "parse_fail"
    SCHEMA_FAIL = "schema_fail"
    RANGE_FAIL = "range_fail"
    API_ERROR = "api_error"


def _extract_json(raw: str) -> Optional[dict]:
    """从模型输出里抽 JSON。容忍 markdown 代码块、控制字符、嵌套引用"""
    if not raw:
        return None
    raw = re.sub(r"[\x00-\x09\x0b-\x1f\x7f]", "", raw)
    code = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if code:
        raw = code.group(1)
    for match in re.finditer(r"\{", raw):
        depth = 0
        for i in range(match.start(), len(raw)):
            if raw[i] == "{":
                depth += 1
            elif raw[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[match.start():i + 1])
                    except json.JSONDecodeError:
                        break
    return None


def validate(parsed: dict) -> tuple[Optional[EAxisScore], Optional[str]]:
    """字段验证 + 范围夹值。返回 (score, fail_reason)"""
    missing = REQUIRED_FIELDS - set(parsed.keys())
    if missing:
        return None, f"{FailReason.SCHEMA_FAIL}: missing {sorted(missing)}"
    try:
        v = float(parsed["valence"])
        a = float(parsed["arousal"])
        t = float(parsed["tension"])
        c = float(parsed["confidence"])
    except (TypeError, ValueError) as e:
        return None, f"{FailReason.SCHEMA_FAIL}: {e}"
    if not -1.0 <= v <= 1.0:
        return None, f"{FailReason.RANGE_FAIL}: valence={v}"
    if not 0.0 <= a <= 1.0:
        return None, f"{FailReason.RANGE_FAIL}: arousal={a}"
    if not 0.0 <= t <= 1.0:
        return None, f"{FailReason.RANGE_FAIL}: tension={t}"
    if not 0.0 <= c <= 1.0:
        return None, f"{FailReason.RANGE_FAIL}: confidence={c}"
    return EAxisScore(
        valence=v,
        arousal=a,
        tension=t,
        confidence=c,
        response_tendency=str(parsed.get("response_tendency") or "engage"),
        growth_delta=str(parsed.get("growth_delta") or "stable"),
    ), None


class EAxisScorer:
    """E 轴评分器 · 给 lmc-5 用户挂任意 LLM 后端

    用法：
        def my_llm(prompt: str, timeout: int) -> str:
            # 调你家的 LLM API，返回字符串
            ...

        scorer = EAxisScorer(
            llm_call=my_llm,
            rubric=open("my_rubric.txt").read(),
            scorer_name="my-llm-v1",
            rubric_version="v1.0",
        )
        score = scorer.score(title="...", content="...")
    """

    def __init__(
        self,
        llm_call: Callable[[str, int], str],
        rubric: str = DEFAULT_RUBRIC,
        scorer_name: str = "unspecified",
        rubric_version: str = "default",
        timeout: int = 60,
        fail_log_path: Optional[Path] = None,
        max_retries: int = 3,
        retry_backoff_s: float = 2.0,
        min_confidence: float = 0.3,
    ):
        """
        Args:
            llm_call: (prompt, timeout_s) → 模型输出字符串。异常应外抛
            rubric: 评分量规，作为 system / 前缀注入
            scorer_name: 记录到 score.scorer，方便追溯模型
            rubric_version: 量规版本号
            timeout: 单次调用超时秒
            fail_log_path: 失败日志路径（None 则不记）
            max_retries: 单条记忆最多重试几次（含首次）。schema_fail / range_fail
                         不重试——模型回答结构错的话再调一次只会再错一次。
            retry_backoff_s: 指数退避基数。第 N 次失败后等 retry_backoff_s ** N 秒
            min_confidence: 最低可接受 confidence 阈值。模型对这次打分把握不到此值
                            就当作 schema 失败丢弃（不污染 E 轴）。默认 0.3。
        """
        if not callable(llm_call):
            raise TypeError(
                f"EAxisScorer: llm_call must be callable, got {type(llm_call).__name__}"
            )
        self.llm_call = llm_call
        self.rubric = rubric
        self.scorer_name = scorer_name
        self.rubric_version = rubric_version
        self.timeout = timeout
        self.fail_log_path = fail_log_path
        self.max_retries = max(1, max_retries)
        self.retry_backoff_s = max(0.0, retry_backoff_s)
        self.min_confidence = max(0.0, min(1.0, min_confidence))

    def _log_fail(self, record_id: Optional[int], category: str, detail: str = "") -> None:
        if not self.fail_log_path:
            return
        try:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            snippet = (detail or "").replace("\n", " ")[:200]
            with open(self.fail_log_path, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] id={record_id} cat={category} detail={snippet}\n")
        except Exception:
            pass

    def build_prompt(self, title: str, content: str) -> str:
        body = (content or "")[:2000]
        return f"{self.rubric}\n\n记忆原文：\n{title}\n\n{body}\n\n按 rubric 打分，只输出 JSON。"

    # 可重试 vs 不可重试的失败类型分类
    # 可重试：网络抖动 / 限流 / 模型偶发空回答 / 偶发非 JSON 输出
    # 不可重试：模型答了结构错的 JSON——再调只会再错，浪费 token
    RETRYABLE_FAILS = (
        FailReason.HTTP_TIMEOUT,
        FailReason.HTTP_ERROR,
        FailReason.EMPTY_RESPONSE,
        FailReason.NO_JSON_IN_TEXT,
    )
    # 注意：low_confidence 不在可重试里——模型再答一次只会同样没把握，浪费 token

    def _attempt_score(
        self,
        prompt: str,
        record_id: Optional[int],
    ) -> tuple[Optional[EAxisScore], Optional[str]]:
        """单次尝试。返回 (score, fail_category)。
        score 非 None → 成功；否则 fail_category 指出失败类型供重试判断。
        """
        try:
            raw = self.llm_call(prompt, self.timeout)
        except TimeoutError:
            return None, FailReason.HTTP_TIMEOUT
        except Exception:
            return None, FailReason.HTTP_ERROR
        if not raw:
            return None, FailReason.EMPTY_RESPONSE
        parsed = _extract_json(raw)
        if parsed is None:
            return None, FailReason.NO_JSON_IN_TEXT
        score, fail = validate(parsed)
        if not score:
            # 提取失败类型前缀（"schema_fail: missing ..." → "schema_fail"）
            cat = (fail or FailReason.PARSE_FAIL).split(":")[0]
            return None, cat
        # 最低置信度门槛 — 模型对自己都没把握的分数不接
        if score.confidence < self.min_confidence:
            return None, f"low_confidence:{score.confidence:.2f}<{self.min_confidence}"
        return score, None

    def score(
        self,
        title: str,
        content: str,
        record_id: Optional[int] = None,
    ) -> Optional[EAxisScore]:
        """打分。失败返回 None（不抛异常）。

        可重试失败（网络/空回答/非 JSON）走指数退避重试，重试到 max_retries 次仍失败才
        log + return None。不可重试失败（schema_fail/range_fail）立刻 log + return None，
        不浪费第二次 token。
        """
        import time
        prompt = self.build_prompt(title, content)
        last_fail: Optional[str] = None
        for attempt in range(self.max_retries):
            result, fail_cat = self._attempt_score(prompt, record_id)
            if result is not None:
                result.scorer = self.scorer_name
                result.rubric_version = self.rubric_version
                return result
            last_fail = fail_cat
            # 不可重试的失败立刻终止
            if fail_cat and not any(fail_cat.startswith(r) for r in self.RETRYABLE_FAILS):
                self._log_fail(record_id, fail_cat,
                               f"non-retryable, attempt={attempt + 1}")
                return None
            # 还有重试机会就退避
            if attempt + 1 < self.max_retries:
                wait = self.retry_backoff_s ** (attempt + 1)
                time.sleep(wait)
        # 重试用完
        self._log_fail(record_id, last_fail or FailReason.PARSE_FAIL,
                       f"retries_exhausted (n={self.max_retries})")
        return None


# === 给 lmc-5 用户的提醒（写在代码里方便他读 docstring）===

INTEGRATION_NOTE = """
集成到 lmc-5 的两个建议：

1. 影子期（建议至少 30 天 / Shadow period: at least 30 days）
   E 轴评分前 30 天不参与 score / rerank / 排序，只挂在记忆上等审。
   原因：E 轴打分不稳定时，让它直接影响检索会污染整个排序系统。
   等覆盖率/稳定率达标再开闸。

2. 不要让 E 轴覆盖事实
   E 轴是"应对姿态"层，不是"什么是真"层。
   tension 高 ≠ 事实失效。response_tendency=withdraw ≠ 不许调用。
   覆盖判定走 Z 轴 z_conflict_audits，E 轴只调注入语气和优先级。
"""


# === 影子期 helper（让"E 轴不参与排序"在代码里也成立）===

def is_in_shadow_period(
    rubric_started_at,
    shadow_days: int = 30,
    now=None,
) -> bool:
    """判断 E 轴是否仍在影子期（不应参与排序）。

    用法：rerank 路径里 `if is_in_shadow_period(scorer_first_seen_at): ignore_e_axis()`。
    rubric_started_at 取你部署/换 rubric 的时间。换 rubric 等于影子期重置。

    给排序层一个明确的门，比"靠人记得别用"靠谱得多。
    """
    from datetime import datetime, timedelta
    if rubric_started_at is None:
        return True  # 没记开始时间默认按影子期处理，保守
    if not isinstance(rubric_started_at, datetime):
        try:
            rubric_started_at = datetime.fromisoformat(str(rubric_started_at))
        except Exception:
            return True
    ref = now or datetime.now()
    if rubric_started_at.tzinfo is not None:
        rubric_started_at = rubric_started_at.replace(tzinfo=None)
    if ref.tzinfo is not None:
        ref = ref.replace(tzinfo=None)
    return (ref - rubric_started_at) < timedelta(days=shadow_days)
