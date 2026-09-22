# my-skill

可复用的 Codex skills，包含照片宠物制作、透明动画预览工具与 Jev 模型路由。

## photo-pet

将参考照片制作成真人风格的 Codex 桌面宠物，也可以按要求使用其他风格。

- 复用已完成的动作，支持继续制作。
- 规范 9 组标准动作与 16 个视线方向。
- 对完整视线行统一校正尺寸，保持角色和姿势一致。
- 从最终透明图集输出 APNG，避免透明 GIF 引起的边缘轮廓。
- 自动核对动画解码后的 RGBA 像素、播放时长和循环设置。

### 安装

将仓库中的 `photo-pet` 文件夹复制到 `$CODEX_HOME/skills/`；未设置 `CODEX_HOME` 时，使用 `~/.codex/skills/`。

例如克隆本仓库后，在 PowerShell 中运行：

```powershell
$skillsDirectory = if ($env:CODEX_HOME) { Join-Path $env:CODEX_HOME 'skills' } else { Join-Path $HOME '.codex/skills' }
New-Item -ItemType Directory -Path $skillsDirectory -Force | Out-Null
Copy-Item -LiteralPath './photo-pet' -Destination $skillsDirectory -Recurse
```

如果已安装同名 skill，先检查现有版本，再决定如何更新。

### 使用

```text
使用 $photo-pet，按既定规范把这张参考图做成完整的桌面宠物。
```

提供参考图或其可访问路径即可。制作前查看 [SKILL.md](photo-pet/SKILL.md) 和 [制作规范](photo-pet/references/production-standard.md)。

### 依赖与范围

完整宠物生成依赖另行安装的 `hatch-pet` skill 及可用的 `imagegen` 能力；本仓库不包含这两个依赖。仅导出动画预览时不需要它们，只需要 Python 3 与 Pillow：

```sh
python -m pip install Pillow
python photo-pet/scripts/render_apng.py --atlas /path/to/spritesheet.webp --output-dir ./previews --prefix my-pet
```

输入必须是带透明通道的静态 Codex v2 图集，尺寸为 `1536×2288`。输出包含 9 组动作、1 个视线循环的 `.png` 动画和 `preview-alpha-validation.json`。退出码为 0 且报告 `ok: true` 表示预览像素和时序检查通过；这不替代角色、动作语义或完整图集验收。

视线预览采用每姿势 180 毫秒，实际桌面宠物视线由指针位置驱动。APNG 需要支持动画 PNG 的查看器。

制作规范中的统一缩放是此工作流的默认约定；使用者明确要求保留原始像素或禁止变换时，应遵循其要求。安装、公开发布等操作仍需遵守当前任务授权。

## jev-router

通过 TypeSafe Jev 判断任务复杂度、是否代码开发、是否需要多步推理等特征，再由可编辑规则选择目标模型。

- 在 YAML 中新增或修改判断场景、分类标准与概率阈值。
- 支持首次命中规则、`all/any` 多条件组合、模型适配加权与 80/20 等流量分配。
- 返回命中规则、概率信号和加权贡献，支持异常备用策略。
- 支持只做路由判断，或调用配置的 Chat Completions 兼容 API 生成回答。

### 安装与使用

将仓库中的 `jev-router` 目录复制到 `$CODEX_HOME/skills/`；未设置 `CODEX_HOME` 时使用 `~/.codex/skills/`。已有同名目录时先检查再更新。

```text
使用 $jev-router，按任务复杂度和是否代码开发配置模型路由。
```

编辑 [路由配置](jev-router/config/router.yaml)，详见 [中文配置与接入说明](jev-router/references/configuration.md) 和 [SKILL.md](jev-router/SKILL.md)。

克隆仓库后可以先运行无需密钥的离线示例：

```sh
uv run --script jev-router/scripts/router.py validate
uv run --script jev-router/scripts/router.py decide --response-file jev-router/examples/complex-response.json
```

默认复杂示例选择 `strong`，简单示例选择 `fast`。真实调用需在本机设置 `TYPESAFE_API_KEY`，以及目标服务的 `MODEL_API_URL`、`MODEL_API_KEY`、`FAST_MODEL`、`CODE_MODEL`、`STRONG_MODEL`。配置文件只保存环境变量名称，不保存密钥。

### 验证与范围

需要 Python 3.10+；`uv run --script` 自动准备 PyYAML 6.x 依赖。运行测试：

```sh
uv run --with 'PyYAML>=6.0.3,<7' python -m unittest discover -s jev-router/tests -v
```

已通过 22 项单元及本地 HTTP 集成测试；未使用真实服务凭据验证分类准确率或付费模型调用。加权适配分不是回答正确率。该执行器生成文本，不会切换当前宿主会话模型，也不会执行代码编辑或 agent 工具循环。

