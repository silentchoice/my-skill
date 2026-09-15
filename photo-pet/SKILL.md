---
name: photo-pet
description: Use when creating or continuing a photo-reference or realistic human Codex desktop pet, following the user's established pet production standard, or fixing outline artifacts in its animated chat preview.
---

# Photo Pet

将参考照片制作成可复用的 Codex v2 桌面宠物；默认采用自然真人比例。用户指定其他风格时按其要求制作，复用相同的生产与交付规范。

## 按任务加载

- **新建、补全或修复宠物内容**：读取已安装的 `hatch-pet/SKILL.md`，使用其生成、提取、组装和检查工具；同时读取 [制作规范](references/production-standard.md)。依赖位于 `$CODEX_HOME/skills/hatch-pet`，未设置 CODEX_HOME 时用 `~/.codex/skills/hatch-pet`。缺失时先查找迁移位置；仍找不到则说明缺失，不伪造工具参数。
- **仅修复聊天预览**：直接使用本 skill 的预览脚本，只需要 Python 与 Pillow，无需启动生成或整套宠物 QA。
- **继续已有任务**：先读取该任务的请求、生成清单和现存检查报告，检查产物实际存在且与当前参考、提示词、参数对应，再从首个未完成依赖继续。

## 本用户的生产默认值

参考图决定角色身份、服装、发型与风格细节，不把历史角色的脸、裙装、姓名或配色固化到新宠物中。照片没有展示的部分应合理补全，并在制作说明中注明。

默认交付完整 v2：9 组标准动作、16 个顺时针视线方向、透明 WebP 图集、pet.json、备份 ZIP、检查记录和保留半透明边缘的 APNG 动画。精确行布局及验收由 hatch-pet 维护。

本规范允许将**同一原始生成行的全部 8 个视线姿势统一缩放和对齐**作为布局校正；保持姿势语义、完整轮廓和已通过的另一行。用户要求逐像素保留或禁止变换时服从该要求。此约定不是逐帧变形、补画、拼接不同生成批次或降低验收标准的授权。

## 无描边预览

GIF 不保留连续的半透明 alpha，不能用 GIF 轮廓判断图集是否需要修复。对比最终透明图集与预览在深浅背景的边缘。若只有 GIF 出现轮廓，重新序列化预览即可；若图集本身也有问题，再走内容修复流程。

从**最终清理后的图集**导出，不能用清理前的 frames 文件夹替代。运行（将占位路径替换为实际路径，Python 可由桌面运行时依赖工具定位）：

```powershell
& $petPython "$photoPetSkill/scripts/render_apng.py" --atlas "$finalAtlas" --output-dir "$previewDir" --prefix "my-pet"
```

脚本输出 9 组动作和视线循环的 `.png` 动画，以及 `preview-alpha-validation.json`；退出码 0 且报告 `ok: true` 表示解码后的 RGBA 像素和时序均与源帧一致。相邻完全重复帧允许合并时长。它不替代动作语义或图集结构检查。

逐帧核对后查看挥手、视线循环和深浅背景边缘，以绝对路径嵌入 APNG。若客户端仅展示首帧，说明播放限制并提供动画文件；不要退回有描边的透明 GIF。需要 GIF 时可另做明确背景色的合成版本。

## 交付

明确报告生成、检查、打包、安装分别是否完成。安装沿用本次任务授权及权限边界；不要为预览格式修复重装宠物。保留原始行、参数和检查记录以便下一次继续，清理可恢复素材应有明确依据，不能为了提速自动删除。
