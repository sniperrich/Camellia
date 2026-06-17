# 网易登录流程对齐设计

日期：2026-06-17

## 背景

Camellia 当前的网易登录实现与 `C:\Users\Administrator\Downloads\OPENNEL_DUMP\Decompiled\OpenNEL\Auth\NeteaseDirectAuthService.cs` 存在行为差异：

- 手机号登录保留了一个额外的“密码登录（手机号@163.com）”分支，这不属于目标实现。
- 手机短信登录请求参数未完全对齐目标实现，缺少 `urs_udid`、`login_for=1` 等字段。
- 当前邮箱和短信登录成功后统一走现有 `WPFLauncherClient.login_with_cookie(...)` 流程；目标实现中，手机短信登录使用显式的 X19 `login-otp` / `authentication-otp` / `interconnection.loginStart` 链路。
- 本地已保存手机号账号可能记录为 `sub_mode=password`，需要兼容读取，但后续应统一收敛到短信登录语义。

本次改动目标是半对齐：后端流程按 Decompiled 的实现调整；GUI 保留“网易邮箱 / 网易手机号”两个入口，但移除手机号密码登录子模式，同时尽量不破坏现有本地账号数据。

## 目标

1. 网易邮箱登录继续保留独立入口，并按目标实现补齐必要的 MKey / SAuth 参数结构。
2. 网易手机号登录仅保留“发送验证码 -> 验证并登录”的两步流程。
3. 手机短信登录的参数处理、ticket 完成登录、SAuth 构造和 X19 后续认证链路对齐 Decompiled。
4. 已保存的手机号账号继续能显示和读取；如果历史记录是 `sub_mode=password`，界面不再暴露该模式，登录行为统一提示为需要手动短信登录，除非已有可复用的 `sauth_json`。
5. 不修改 4399 登录和手工 SAuth 登录行为。

## 非目标

- 不引入 Decompiled 里的二维码登录。
- 不把 GUI 完全重做成 OpenNEL 的网页状态机样式。
- 不清洗或迁移用户本地 `accounts.json` 中的旧字段结构，只做运行时兼容。

## 方案概览

推荐方案：半对齐。

- API 层按 Decompiled 对齐手机短信登录参数与认证后续流程。
- GUI 层移除“密码登录”子 tab，只保留手机号 + 验证码输入和发送验证码按钮。
- 存储层保留 `sub_mode` 字段读取兼容，但 `netease_phone` 的呈现和保存统一视为短信登录。

这样可以把主要行为对齐目标实现，同时避免为已存在的保存账号做破坏性迁移。

## 详细设计

### 1. `camellia/api/netease.py`

#### 1.1 MKey 基础参数

保持现有 `app_channel=netease` 的 MKey 请求方向，但按 Decompiled 收敛字段语义：

- 手机短信发送验证码时增加 `urs_udid = unique_id`
- 短信验证码校验时增加：
  - `login_for = "1"`
  - `urs_udid = unique_id`
- 短信完成登录时增加：
  - `login_for = "1"`
  - `urs_udid = unique_id`

邮箱登录继续复用现有 MKey 邮箱接口，但需要统一 SAuth 构造字段，避免与目标实现继续漂移。

#### 1.2 SAuth 构造

当前 `build_sauth_json(...)` 输出字段过少。按 Decompiled 对齐为显式构造：

- `gameid = "x19"`
- `login_channel = <真实登录渠道，缺省 netease>`
- `app_channel = "a50_sdk_cn"`
- `platform = "pc"`
- `sdkuid = <MPay user id 或兼容值>`
- `sessionid = <token>`
- `sdk_version = "4.17.2"`
- `udid = <device_id>`
- `deviceid = <device_id>`
- `aim_info = <与当前 PC 启动器一致的 JSON>`
- `client_login_sn = <大写 GUID>`
- `gas_token = ""`
- `extra_channel = ""`
- `source_platform = "pc"`
- `ip = ""`
- `get_access_token = "1"`

这里不要求把所有设备采集字段都做满，只要求产出的 `sauth_json` 结构与目标实现兼容。

#### 1.3 手机短信登录完成后的认证流

新增一条显式 X19 OTP 认证链路，用于替代当前“构造 sauth_json 后直接交给 `WPFLauncherClient.login_with_cookie(...)`”的手机短信成功路径：

1. 使用短信 `ticket` 完成 MKey 登录，拿到 `user.id`、`user.token`、`user.login_channel`
2. 构造对齐后的 `sauth_json`
3. 调用 X19 `/login-otp`
4. 调用 X19 `/authentication-otp`
5. 必要时调用现有互联启动前置接口，保证状态与现有客户端一致
6. 产出当前 GUI 仍可消费的登录结果

实现策略：

- 尽量复用现有 `HttpClient`、加密和 `WPFLauncherClient` 周边基础设施
- 新增独立辅助函数，例如：
  - `build_netease_sauth_json(...)`
  - `login_otp_with_sauth(...)`
  - `authentication_otp_with_sauth(...)`
  - `continue_phone_login_via_x19(...)`
- 让手机短信登录最终返回与当前 GUI 对接所需的统一结果，避免 GUI 直接知道 OTP 细节

#### 1.4 邮箱登录

邮箱登录仍保留“邮箱 + 密码 -> MKey 登录”的入口，但对齐以下点：

- `sdkuid` 优先取 MKey user id
- `login_channel` 优先取 MKey 返回值，缺省为 `netease`
- 生成的 `sauth_json` 使用与手机登录一致的新字段结构

邮箱登录本次不强制改成显式 OTP 链路；优先保证产物结构和参数处理与目标实现一致，并维持当前 GUI 集成成本可控。

### 2. `camellia/gui/pages/login_page.py`

手机号登录界面收敛为单一路径：

- 删除 `短信验证码 / 密码登录` 子 tab
- 删除 `netease_phone_stack`
- 删除 `netease_phone_pass`
- 保留：
  - 手机号输入框
  - 验证码输入框
  - 发送验证码按钮

交互规则：

- 手机号输入和验证码输入始终可见
- 点击“发送验证码”只触发发送，不切换为其他子页面
- 登录时要求同时填写手机号和验证码
- 界面标签仍保持“网易邮箱”“网易手机号”

这是 GUI 半对齐，不去复制网页端的双阶段页面切换，只保留必要语义。

### 3. `camellia/gui/main_window.py`

#### 3.1 手动登录

`_handle_login_impl` 中的 `mode == "netease_phone"` 分支调整为：

- 不再读取 `netease_phone_login_mode()`
- 不再支持 `phone@163.com` 邮箱密码回退
- 统一校验手机号和验证码
- 调用新的手机短信登录后端流程

`_handle_send_sms` 保持入口不变，但错误提示与参数校验文案同步更新。

#### 3.2 已保存账号加载

`_load_saved_account` 中：

- `netease_phone` 仍读取历史账号
- 无论历史 `sub_mode` 是 `password` 还是 `sms`，都统一切到手机号短信登录界面
- 只回填手机号
- 不再回填任何手机号密码字段
- 清空验证码输入框

#### 3.3 已保存账号自动登录

`_handle_saved_login_impl` 中对 `netease_phone` 的处理改为：

- 先尝试使用已保存 `sauth_json`
- 若 `sauth_json` 失效，则不再使用 `sub_mode=password` + `phone@163.com` 自动回退
- 统一报错提示：历史手机号账号需要手动短信验证码登录

这样历史数据仍然可见、可点，但不会继续依赖已经被移除的旧路径。

#### 3.4 自动保存

`_auto_save_account` 中：

- `netease_phone` 保存时不再区分 `password` / `sms`
- 统一保存为 `SavedAccount.new_netease_phone(phone, login_mode="sms", ...)`
- 不再保存手机号密码

### 4. `camellia/gui/storage.py`

保留数据结构兼容，但展示文案收敛：

- `SavedAccount.new_netease_phone(...)` 仍接受 `login_mode` 参数，避免影响旧调用；内部统一收敛到 `sms`
- `label` 中的手机号账号不再显示“网易手机号（密码）”
- 历史 `sub_mode=password` 账号也统一显示为“网易手机号：<号码>”

这样可以避免界面继续暴露已经废弃的模式。

## 错误处理

- MKey 返回 JSON 错误时继续走统一的 `_format_mpay_error(...)`，保留 `reason` / `code` / `verify_url`
- 手机短信登录 OTP 阶段如果失败，应包装成和现有 GUI 兼容的错误字符串
- 历史手机号密码账号的保存登录失败时，明确提示：
  - 已保存的旧手机号密码模式已废弃
  - 如保存的 `sauth_json` 不可用，请手动获取短信验证码重新登录

## 测试与验证

由于仓库当前没有完整自动化测试，本次采用“补最小测试 + 手动验证”的方式。

建议新增或调整的自动化覆盖：

- `camellia/api/netease.py`
  - 手机短信发送参数包含 `urs_udid`
  - 手机短信校验参数包含 `login_for=1`、`urs_udid`
  - 手机短信完成参数包含 `login_for=1`、`urs_udid`
  - 新的 `sauth_json` 结构包含关键字段
- `camellia/gui/storage.py`
  - 历史 `sub_mode=password` 的手机号账号标签统一显示为“网易手机号”

手动验证清单：

1. 网易邮箱登录成功
2. 网易手机号发送验证码成功
3. 网易手机号使用短信验证码登录成功
4. 历史 `sub_mode=password` 的手机号账号能显示、能载入手机号，但不会再出现密码输入模式
5. 历史手机号账号在 `sauth_json` 失效后，会提示手动短信登录，而不是尝试旧密码回退
6. 4399 登录、SAuth 登录不受影响

## 风险与权衡

- 手机短信登录后续 OTP 链路若与当前 `WPFLauncherClient` 的会话模型不一致，可能需要增加适配层。
- 历史依赖手机号密码自动回退的用户会失去该能力；这是本次方案刻意接受的兼容性收缩。
- 邮箱登录本次不完全改造成 Decompiled 的整条 Continue 流，只对齐参数处理和 `sauth_json` 结构；这是为了控制改动面。

## 交付边界

本次完成后，Camellia 的网易登录应满足：

- GUI 上存在“网易邮箱”“网易手机号”两个入口
- 网易手机号只支持短信验证码登录
- 手机短信登录请求参数、ticket 完成和后续认证语义对齐 Decompiled
- 历史手机号密码账号不被删除，但不再继续走旧逻辑
