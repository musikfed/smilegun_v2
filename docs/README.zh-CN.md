# SMILE·GUN v2 — 中文快速指南

SMILE·GUN 是一款本地浏览器体感街机游戏，通过摄像头识别手势、眨眼、张嘴和微笑。主菜单可以选择语言，浏览器会保存选择。

**操作：**手移动方块；握拳单发；张嘴散射；眨眼为 SUPER 充能；微笑随机增加 10–25 秒、+1 生命并获得 45 秒护甲；SUPER 100% 后再次微笑清场；✌ 开启幽灵模式；👐 暂停/继续。

四个回合：**靶场 → 银河 → 闪避 → 横向飞行**。奖励后的剩余时间最多 50 秒。

Windows 启动：

```powershell
cd D:\smilegun
uv run --python 3.12 python app.py
```

手机通过 Wi‑Fi 使用前置摄像头时，请运行 `run_mobile.ps1`，信任 `.smilegun-certs` 中的本地证书，再打开脚本显示的 HTTPS 地址。完整技术文档见根目录英文 `README.md`。
