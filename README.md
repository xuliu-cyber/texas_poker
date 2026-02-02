# Texas Hold'em (LAN Web)

一个可在局域网多人联机游玩的网页版德州扑克（简化规则版），基于 Flask + Socket.IO。

## 1) 安装

建议使用虚拟环境：

```powershell
cd d:\code\PYTHON\texas_holdem_lan
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

如果你遇到 `collections.MutableMapping` 相关报错（旧版 `tornado` 与 Python 3.10+ 不兼容），先执行：

```powershell
pip install -U tornado
```

## 2) 运行（局域网联机）

```powershell
cd d:\code\PYTHON\texas_holdem_lan
.\.venv\Scripts\Activate.ps1
python server.py --host 0.0.0.0 --port 5000
```

同一局域网内其他设备/电脑在浏览器打开：

- `http://<你的电脑局域网IP>:5000`

你的局域网 IP 可用：

```powershell
ipconfig
```

## 3) 玩法

- 输入昵称与房间号进入同一房间。
- 至少 2 人加入后，所有人点 `Ready`，房主可点 `Start`（或任意人点 Start 也可）。
- 下注轮支持：Fold / Check / Call / Raise（简化版）。

## 4) 说明

这是一个“能玩”的最小实现：
- 单桌（每房间一桌），最多 9 人。
- 结算使用 `treys` 评估牌型。
- UI 简洁，方便你后续美化、加动画、加语音等。
