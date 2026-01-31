# Repository Guidelines

## 项目结构与模块组织
- `camellia/` 为核心库：`api/` 处理网易/WPF 启动器接口，`crypto/` 负责加解密，`mc/` 管理协议与代理，`gui/` 为 PySide6 界面（布局在 `app.py`，控件在 `widgets.py`，异步任务在 `workers.py`），`plugins/` 存放协议/插件实现，`models/` 存放数据模型。
- 根目录入口：`camellia_gui.py` 启动跨平台 Qt GUI，`camellia_cli.py` 走命令行调试流程。`reference/` 为逆向资料与对比文件，`logs/`、`logs2/` 存运行日志。
- 新功能按职责放置：界面逻辑留在 `gui/`，网络/协议放 `mc/` 或插件，避免跨层耦合。

## 构建、运行与开发命令
- 安装依赖：`python -m pip install -r requirements.txt`
- 启动 GUI：`python camellia_gui.py`
- 启动 CLI：`python camellia_cli.py`（便于协议、代理调试）
- 查看日志：运行后检查 `logs/` 或 `logs2/`，提交问题时附关键片段。

## 编码规范与命名
- Python 3.10+，4 空格缩进，UTF-8 源码；函数/变量用 `snake_case`，类用 `PascalCase`，常量全大写。
- 模块保持单一职责；UI 文本统一中文，样式复用 `gui/theme.py`；跨平台优先使用 PySide6 原生控件。
- 不提交账号、令牌或密钥；本地配置写入未纳入版本控制的文件（如 `.env.local`）。

## 测试与验证
- 当前无自动化测试；新增功能优先补充 `tests/` 下的 `test_*.py`（可用 `unittest` 或 `pytest`）。
- 手动验证清单：登录与历史账号选择、服务器列表滚动加载/分页、插件启用与心跳、代理连接稳定性、界面在不同分辨率下的布局一致性。

## 提交与 PR 指南
- 提交信息用简短祈使句，可遵循 Conventional Commits：如 `feat: improve server scroll load`、`fix: heypixel heartbeat interval`。
- PR 描述需包含变更概述、运行/验证命令、影响的配置或端口、相关日志片段/截图；涉及协议调整时注明版本与兼容性。
- 引入新依赖时更新 `requirements.txt` 并说明用途。
