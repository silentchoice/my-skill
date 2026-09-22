# 配置、运行与接入

所有命令在 PowerShell 中运行。先把 `$skillDir` 指向实际 skill 目录：

```powershell
$skillDir = Join-Path $env:USERPROFILE '.codex/skills/jev-router'
uv run --script "$skillDir/scripts/router.py" validate
uv run --script "$skillDir/scripts/router.py" decide --response-file "$skillDir/examples/complex-response.json"
```

需要 Python 3.10+。`uv run --script` 根据脚本元数据提供 PyYAML 6.x；首次运行需要下载依赖。
也可在已有 Python 环境安装 `PyYAML>=6.0.3,<7` 后直接 `python scripts/router.py ...`。
离线示例不调用任何 API；默认复杂示例选 strong，简单示例选 fast。

## 连接信息

默认从环境变量读取：

| 环境变量 | 内容 |
|---|---|
| TYPESAFE_API_KEY | TypeSafe 的 Jev API 密钥 |
| MODEL_API_URL | 目标服务完整 Chat Completions URL，包含 `/v1/chat/completions` 路径 |
| MODEL_API_KEY | 目标服务密钥 |
| FAST_MODEL | 轻量模型真实 ID |
| CODE_MODEL | 代码模型真实 ID |
| STRONG_MODEL | 推理模型真实 ID |

把这些变量设置到运行脚本的进程环境。可以在可信本地终端读取密钥，避免放入命令历史或聊天：

```powershell
$jevSecret = Read-Host 'TypeSafe API key' -AsSecureString
$env:TYPESAFE_API_KEY = [System.Net.NetworkCredential]::new('', $jevSecret).Password
$modelSecret = Read-Host 'Model API key' -AsSecureString
$env:MODEL_API_KEY = [System.Net.NetworkCredential]::new('', $modelSecret).Password
$env:MODEL_API_URL = Read-Host 'Complete Chat Completions endpoint'
$env:FAST_MODEL = Read-Host 'Fast model ID'
$env:CODE_MODEL = Read-Host 'Code model ID'
$env:STRONG_MODEL = Read-Host 'Strong model ID'
uv run --script "$skillDir/scripts/router.py" validate --check-execution
uv run --script "$skillDir/scripts/router.py" decide --input-file "$skillDir/examples/task.json"
uv run --script "$skillDir/scripts/router.py" execute --input-file "$skillDir/examples/task.json"
```

这些环境变量仅对该终端及其子进程有效。若由 agent 运行，需在该 agent 启动环境中设置，
或使用已配置的凭据注入机制。不要把已有密钥复制进 YAML、日志或聊天。

模型可使用不同端点：给每个模型设置不同的 `endpoint_env`、`api_key_env`。
也支持直接填写非密钥值 `model` 和 `endpoint`；分别与 `model_env`、`endpoint_env` 互斥。
无鉴权的本地端点可省略 `api_key_env`。远程端点必须 HTTPS，本地 HTTP 仅限
`localhost`、`127.0.0.1`、`::1`。不跟随重定向，不支持 URL 内密钥或查询参数。

`parameters` 是目标模型额外请求字段，例如供应商支持的 `temperature`、
`max_tokens` 或 `reasoning_effort`。参数支持情况由目标服务决定；路由器不会自动翻译。
不能覆盖 `model/messages/stream/n` 或开启工具调用。每次生成只发一个请求，不自动重试。
当前不适配原生 Responses、Anthropic Messages 等不同协议；这些服务需兼容网关或另加适配器。

## 编辑场景

`scenarios` 的每个键是场景 ID，可添加、删除或修改。ID 和 Choice 选项限字母、数字、`_`、`-`，
禁止点号以免和字段路径冲突。`instructions` 必须写完整问题，Jev 不会看到场景 ID。
所有场景合并为一次 API 请求，结果相互独立；复杂决策交给本地路由规则组合。

```yaml
scenarios:
  needs_search:
    type: noul
    instructions: 回答任务是否需要查阅最新信息或外部资料？
    criteria:
      "true": 必须查阅当前状态或输入中未提供的具体资料。
      "false": 现有上下文足以回答。
```

上面是添加到现有 `scenarios` 下的片段，不是完整配置。YAML 的 `true/false` 键必须加引号。
`Choice.criteria` 是 1–255 个选项的映射；`Score.criteria` 是 2–10 个有序文字等级；
`Noul.criteria` 可省略。配置支持 JSON，因为 JSON 也是 YAML 子集。
不支持 YAML merge key；拒绝重复键，避免规则被悄悄覆盖。

可在规则中引用的字段：

| 类型 | 字段示例 | 含义 |
|---|---|---|
| Choice | complexity.probabilities.complex | 该选项概率 |
| Choice | complexity.confidence | 整体分布集中程度所对应的置信度 |
| Choice | complexity.choice | 最高概率选项名；只支持 eq/ne |
| Noul | coding.noul | 是的概率；0.5 表示不确定，不是“中等程度” |
| Score | context_quality.score | 0 到等级数减一的分数 |
| Score | context_quality.normalized | 本地计算 score / (等级数 - 1) |
| Score | context_quality.probabilities.2 | 第 2 级的概率（下标从 0 开始） |
| Score | context_quality.confidence | 评分分布的置信度 |

增加场景后，离线 fixture 也必须增加对应答案。缺失答案不能默认当作零。

## 阈值路由

`routing.mode: rules`，按 `rules` 顺序选择第一个命中的规则。
复杂任务应置于一般代码任务之前；是否这样排序由业务决定。
数值比较支持 `gt/gte/lt/lte/eq/ne`；推荐连续概率用区间，避免依赖小数完全相等。
组支持嵌套 `all/any`，不允许空组，每个组仅能选择一种组合方式。

```yaml
- id: code-or-deep-reasoning
  any:
    - field: coding.noul
      op: gte
      value: 0.80
    - all:
        - field: reasoning.noul
          op: gte
          value: 0.85
        - field: complexity.confidence
          op: gte
          value: 0.50
  target: strong
```

该片段是一个规则列表项。规则必须包含 `target` 或 `traffic`，不能同时包含二者。
可在 `routing.require` 填一个相同结构的条件；不满足时直接 fallback，不执行任何规则或加权。
没有配置置信度门槛时，低 confidence 不会隐式覆盖概率阈值。

## 模型适配权重

把 `routing.mode` 改成 `weighted`，脚本使用同一文件的 `routing.weighted`。

```text
适配分(model) = bias + Σ[signal(field) × weight]
```

`terms` 可以引用任意已定义的数值字段。权重允许负数，不强制和为 1；
不同模型的分数要由配置者设计为可比较尺度。Score 跨不同等级数量时优先用 `.normalized`。
`when` 可限制候选资格，例如仅在编码概率高时让代码模型参与。
`tie_break` 必须列出所有候选各一次，分数相同时排前者优先；
`min_margin` 规定第一与第二名最小分差，差值小于门槛时 fallback。
仅一个候选时直接选它；没有合格候选则 fallback。

默认复杂 fixture：strong 分数 = 0.7×0.7 + 0.3×0.9 = 0.76。
这是适配分，不是校准置信度、成功概率或复杂概率。不要把它与 Jev 的 confidence 混用。

## 模型流量比例

在某个规则中，用 `traffic` 替换 `target`：

```yaml
traffic:
  coder: 80
  strong: 20
```

意味着该规则命中后约 80% 请求选择 coder、20% 选择 strong。可以使用任意非负权重，
总和必须大于零；零权重模型不会被选。它不表示任务有 80% 概率属于代码开发。

提供 `--request-id order-123` 时，由规则 ID 和请求 ID 的 SHA-256 确定桶位；
同一配置、同一 ID 结果可重复。省略 ID 时使用随机桶位。比例是大量请求上的期望，
不是每 10 个请求必须严格 8/2。调整权重或模型顺序可能改变已有 ID 的分配结果。
不要用流量分配让不具备任务能力的模型参与，先用规则筛选适用任务。

## 输出、错误和验证

`selected` 是配置中的模型别名，`model` 为解析到的模型 ID（未配置环境时可为 null）。
`signals` 是经校验的扁平信号，`trace` 解释规则条件或逐项加权贡献，
`scores` 仅是适配分。`source` 为 `fixture`、`live` 或 `live_error`。
默认不写磁盘日志、不回显原始任务、不输出密钥或 HTTP 错误响应正文。
`execute` 的成功输出包含目标模型生成的文本，该文本可能包含输入中的业务内容。

主要 reason：`rule_match`、`weighted_score`、`no_match`、`guard_failed`、
`small_margin`、`no_eligible_model`、`jev_error`。`on_jev_error: fallback` 时，
Jev 网络/鉴权/响应错误选择备用模型，清楚标记 `live_error`；`execute` 将调用该备用模型。
`on_jev_error: stop` 时这些错误停止流程。配置错误始终停止，不能靠 fallback 掩盖。

退出码：0=校验/路由成功或已明确返回备用结果；2=配置/输入/分类停止；
3=目标模型调用失败。调用方若不接受分类失败后的备用结果，使用 stop 或检查 source。
目标模型失败不再递归选择其他模型，也不自动重试，避免重复费用。
`finish_reason` 非 `stop`（截断、工具调用、过滤）不会被报告为完成。

```powershell
uv run --with 'PyYAML>=6.0.3,<7' python -m unittest discover -s "$skillDir/tests" -v
```

测试使用本地 HTTP 服务器和人工 fixture，验证路由/线协议，不代表真实 Jev 分类准确率。
上线前用带有期望模型标签的实际任务，检查误路由率、回答质量、分类+重试费用及总延迟，
再调整标准、阈值和权重。

核对接口日期：2026-09-22。官方参考：
[Jev HTTP API](https://docs.typesafe.ai/api)、
[Choice](https://docs.typesafe.ai/primitives/choice)、
[Score](https://docs.typesafe.ai/primitives/score)、
[Noul](https://docs.typesafe.ai/primitives/noul)、
[Chat Completions 协议](https://developers.openai.com/api/reference/resources/chat)。
