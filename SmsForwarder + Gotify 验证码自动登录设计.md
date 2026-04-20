# SmsForwarder + Gotify 验证码自动登录设计

## 1. 文档目的
本文档用于设计一套基于 **SmsForwarder + Gotify** 的短信验证码自动登录方案，解决以下场景问题：

- 登录系统需要短信验证码
- 验证码发送到手机
- 手机不在电脑旁边
- 电脑没有公网地址
- 希望本地程序能够自动获取验证码并完成登录

该方案的核心思想是：

**手机接收短信 → SmsForwarder 转发到 Gotify → 本地程序从 Gotify 获取验证码 → 自动填入登录页完成登录**

---

## 2. 适用场景

本方案适用于以下场景：

- 业务系统使用短信验证码登录
- 手机位于异地或不方便人工查看
- 本地电脑无法直接暴露公网 Webhook
- 希望验证码进入自动化流程，而不是人工复制
- 希望将短信验证码能力接入自动化通报系统中的 `login_handler`

---

## 3. 总体方案

## 3.1 方案概述
系统由四部分组成：

1. **目标业务系统**
   - 登录时发送短信验证码

2. **Android 手机 + SmsForwarder**
   - 接收短信
   - 按规则筛选验证码短信
   - 将短信内容转发到 Gotify

3. **Gotify 服务**
   - 作为公网可访问的消息中转站
   - 接收手机转发的短信内容
   - 提供给本地程序读取

4. **本地自动登录程序**
   - 发起登录
   - 请求发送验证码
   - 轮询 Gotify 获取对应短信
   - 提取验证码
   - 自动填写并提交

---

## 3.2 核心链路

```text
本地程序请求登录
→ 系统发送短信验证码
→ 手机收到短信
→ SmsForwarder 按规则转发到 Gotify
→ 本地程序轮询 Gotify
→ 匹配验证码短信
→ 提取验证码
→ 自动填入并提交
→ 登录成功
```

---

## 4. 为什么选择 Gotify

当电脑没有公网地址时，手机无法直接把验证码推送到本地电脑。  
因此需要一个公网中转服务。

Gotify 在本方案中的定位是：

- 一个公网可访问的消息缓冲中转站
- 一个轻量的验证码消息接收器
- 一个便于本地程序轮询的统一入口

相较于“手机直接推到本地 Webhook”，该方案更适合以下情况：

- 电脑没有公网 IP
- 本地不方便搭建反向隧道
- 希望快速打通验证码自动获取链路
- 验证码只需短时中转，不需要复杂持久化

---

## 5. 系统组成

## 5.1 手机侧
### 组件
- Android 手机
- SmsForwarder

### 职责
- 接收短信
- 按发送方、关键词等规则筛选验证码短信
- 将短信内容推送到 Gotify

---

## 5.2 中转侧
### 组件
- Gotify Server
- 独立的 Gotify Application

### 职责
- 接收验证码消息
- 为本地登录程序提供统一读取入口
- 暂存短信内容，供本地程序拉取

---

## 5.3 本地程序侧
### 组件
- `login_handler`
- `otp_provider_gotify`
- 浏览器自动化或请求登录逻辑

### 职责
- 发起登录
- 请求系统发送验证码
- 从 Gotify 获取验证码消息
- 提取验证码
- 自动提交验证码
- 登录成功后清理状态

---

## 6. 模块设计

## 6.1 login_handler
### 职责
管理整个验证码登录过程。

### 建议接口
```python
start_login() -> None
request_sms_code() -> None
wait_for_otp() -> str
submit_sms_code(code: str) -> bool
save_session() -> None
```

### 执行职责
- 打开登录页
- 输入账号信息
- 点击“发送验证码”
- 调用 OTP 提供器等待验证码
- 自动输入验证码
- 提交登录表单
- 保存 cookie / session / token

---

## 6.2 otp_provider_gotify
### 职责
封装与 Gotify 交互的所有逻辑。

### 建议接口
```python
fetch_messages() -> list
find_matching_otp(messages: list, login_key: str, trigger_time) -> str | None
extract_otp(text: str, code_regex: str) -> str | None
wait_for_otp(login_key: str, timeout_seconds: int, poll_interval_seconds: int) -> str | None
ack_message(message_id: str) -> None
```

### 执行职责
- 查询 Gotify 中的新消息
- 根据规则筛选本次登录对应短信
- 从短信正文中提取验证码
- 返回验证码
- 登录成功后将消息标记为已使用或删除

---

## 6.3 sms_rule_manager
### 职责
管理 SmsForwarder 的匹配规则。

### 负责内容
- 发送方过滤
- 关键词过滤
- 消息标题格式约定
- 消息正文模板约定

---

## 6.4 gotify_message_parser
### 职责
把 Gotify 消息解析成结构化验证码对象。

### 建议输出结构
```python
{
    "message_id": "123",
    "sender": "10086",
    "title": "system_a otp",
    "message": "您的验证码为123456，5分钟内有效",
    "otp_code": "123456",
    "receive_time": "2026-04-18 09:00:03"
}
```

---

## 7. 消息格式设计

为了便于本地程序准确匹配验证码，建议 SmsForwarder 转发到 Gotify 时统一格式。

## 7.1 标题格式建议
```text
{login_key} OTP
```

示例：

```text
system_a OTP
```

---

## 7.2 正文格式建议
```text
sender=系统名称
time=2026-04-18 09:00:03
content=您的验证码为123456，5分钟内有效
```

或者直接发送短信全文，但建议至少保留：

- 短信发送方
- 收到时间
- 短信正文

---

## 7.3 为什么要约定格式
这样做有以下好处：

- 便于本地程序按 `login_key` 精确匹配
- 防止多个系统验证码串号
- 便于按时间窗口过滤
- 便于调试与日志记录

---

## 8. JSON 配置设计

建议在登录配置中增加 OTP 配置段。

```json
{
  "login_key": "system_a",
  "otp_mode": "gotify",
  "otp_config": {
    "gotify_url": "https://gotify.example.com",
    "app_token": "your_app_token_for_push_side",
    "client_token": "your_client_token_for_fetch_side",
    "title_prefix": "system_a OTP",
    "allowed_senders": ["10086", "系统名称"],
    "required_keywords": ["验证码", "动态码", "校验码"],
    "code_regex": "\\b(\\d{4,8})\\b",
    "timeout_seconds": 90,
    "poll_interval_seconds": 3,
    "message_ttl_seconds": 180,
    "delete_after_success": true
  }
}
```

---

## 9. 字段说明

### gotify_url
Gotify 服务地址。

### app_token
手机侧推送消息使用的 token。

### client_token
本地程序读取消息使用的 token。

### title_prefix
用于匹配本次系统验证码消息的标题前缀。

### allowed_senders
允许的短信发送方列表。

### required_keywords
必须包含的关键词，用于过滤非验证码短信。

### code_regex
验证码提取正则。

### timeout_seconds
本地程序等待验证码的最长时间。

### poll_interval_seconds
轮询 Gotify 的时间间隔。

### message_ttl_seconds
只接受一定时间窗口内的新消息。

### delete_after_success
登录成功后是否删除已消费消息。

---

## 10. 时序设计

## 10.1 正常时序

```text
本地程序启动登录
→ 打开登录页
→ 输入账号
→ 点击发送验证码
→ 记录 trigger_time
→ 手机收到短信
→ SmsForwarder 根据规则转发到 Gotify
→ Gotify 保存消息
→ 本地程序轮询 Gotify
→ 发现标题和时间都匹配的消息
→ 提取验证码
→ 自动输入验证码
→ 提交登录
→ 登录成功
→ 删除消息或标记已消费
→ 保存会话
```

---

## 10.2 失败时序

### 情况 1：未收到短信
- 本地轮询超时
- 记为登录失败
- 进入重试逻辑

### 情况 2：收到短信但提取失败
- 消息不符合格式
- 没有匹配到验证码
- 进入重试逻辑

### 情况 3：Gotify 不可访问
- 网络异常
- 服务异常
- 进入重试逻辑

### 情况 4：验证码已过期
- 虽收到短信，但系统验证码失效
- 重新触发发送验证码

---

## 11. 匹配规则设计

为避免验证码串用，不能只取“最新一条消息”。  
建议按以下条件综合匹配：

### 11.1 标题匹配
标题必须匹配当前 `login_key` 对应的前缀。

例如：
- 当前系统是 `system_a`
- 只接受标题为 `system_a OTP` 的消息

### 11.2 时间窗口匹配
只接受 `trigger_time` 之后到 `timeout_seconds` 之内收到的消息。

### 11.3 关键词匹配
正文中必须包含：
- 验证码
- 动态码
- 校验码
等关键词之一

### 11.4 发送方匹配
发送方必须在 `allowed_senders` 列表中。

### 11.5 正则提取匹配
正文中必须能提取出 4~8 位验证码。

---

## 12. 安全设计

验证码属于高敏感信息，必须做最小化暴露。

## 12.1 传输安全
- Gotify 必须启用 HTTPS
- 不要使用明文 HTTP 暴露验证码消息

## 12.2 权限隔离
- 验证码中转建议使用独立 Gotify 用户
- 验证码中转建议使用独立 Application
- 不要与普通通知复用同一个消息流

## 12.3 规则收窄
SmsForwarder 只转发满足规则的短信：
- 指定发送方
- 指定关键词
- 指定消息模板

## 12.4 一次性消费
验证码一旦成功使用，消息应立即删除或标记为已消费。

## 12.5 日志脱敏
日志中不要完整记录验证码，可写为：

```text
收到验证码：12****（已脱敏）
```

---

## 13. 本地轮询策略

建议使用轮询而不是永久监听，便于和登录动作严格绑定。

## 13.1 轮询参数建议
- 超时时间：60~90 秒
- 轮询间隔：2~5 秒

## 13.2 轮询逻辑建议
```python
start = now()
while now() - start < timeout_seconds:
    messages = fetch_messages()
    code = find_matching_otp(messages, login_key, trigger_time)
    if code:
        return code
    sleep(poll_interval_seconds)
return None
```

---

## 14. 失败重试策略

短信验证码登录失败后，可以按以下策略处理：

### 14.1 可重试场景
- Gotify 无法访问
- 没有收到验证码
- 提取失败
- 验证码输入后登录失败
- 验证码过期

### 14.2 重试方式
- 重新触发发送验证码
- 重新进入轮询
- 重新提取并提交

### 14.3 最大重试次数
建议与通报系统登录逻辑保持一致，例如：

- 单次登录最大重试 3 次
- 每次间隔 10~20 秒

---

## 15. 接入自动化通报系统的方式

本方案应作为登录模块中的一个 OTP 实现方式接入，而不是单独成为主流程。

## 15.1 在 login_handler 中的定位
登录方式可以抽象为：

- `password_login`
- `smsforward_gotify_login`
- `manual_login`

其中本方案属于：

- `smsforward_gotify_login`

---

## 15.2 与原系统的关系
在自动化通报系统中，它属于：

- **模块归属**：`login_handler`
- **子能力归属**：`otp_provider`

推荐结构：

```text
handlers/
├── login_handler.py
└── otp_provider_gotify.py
```

---

## 16. 代码结构建议

```text
auto_notify/
├── handlers/
│   ├── login_handler.py
│   └── otp_provider_gotify.py
├── core/
│   ├── config_manager.py
│   └── state_manager.py
└── runtime/
    └── logs/
```

---

## 17. 关键接口建议

## 17.1 login_handler.py
```python
def login_with_smsforward_gotify(task_config) -> object:
    pass
```

### 内部步骤
1. 打开登录页
2. 输入账号
3. 点击发送验证码
4. 记录发送时间
5. 调用 `otp_provider_gotify.wait_for_otp(...)`
6. 自动填写验证码
7. 提交登录
8. 返回 session

---

## 17.2 otp_provider_gotify.py
```python
def wait_for_otp(otp_config: dict, login_key: str, trigger_time) -> str | None:
    pass

def fetch_gotify_messages(otp_config: dict) -> list:
    pass

def match_and_extract_code(messages: list, otp_config: dict, login_key: str, trigger_time) -> tuple:
    pass

def delete_message(otp_config: dict, message_id: str) -> bool:
    pass
```

---

## 18. 最小落地方案

如果你现在的目标是尽快跑通，建议第一版先实现最小闭环：

1. 搭建一个 Gotify 服务
2. 在 Gotify 中创建独立验证码 application
3. 手机安装并配置 SmsForwarder
4. 配置规则只转发验证码短信
5. 本地程序实现：
   - 请求发送验证码
   - 轮询 Gotify
   - 正则提取验证码
   - 自动提交验证码
6. 登录成功后删除验证码消息

这样就能完成第一版自动验证码登录。

---

## 19. 风险与限制

### 19.1 规则配错会误转发短信
若 SmsForwarder 过滤规则过宽，可能把非验证码短信也推到 Gotify。

### 19.2 多系统并发登录时容易串号
若没有 `login_key`、标题规则和时间窗口约束，多个验证码可能混淆。

### 19.3 Gotify 消息堆积
若不及时清理历史消息，轮询匹配复杂度会增加。

### 19.4 验证码有效时间短
若系统验证码有效时间极短，轮询间隔过大会导致登录失败。

---

## 20. 设计结论

对于“手机在外面、电脑没有公网地址、希望验证码自动登录”的场景，  
**SmsForwarder + Gotify** 是一套可行且成本较低的实现方案。

该方案的核心优点是：

- 不依赖本地公网地址
- 不需要额外编写完整中转服务
- 容易接入现有自动化系统
- 便于后续扩展到统一 OTP 提供器框架

建议将本方案作为自动化通报系统中 `login_handler` 的一种验证码登录实现，并通过 `otp_provider_gotify` 模块完成与 Gotify 的交互。
