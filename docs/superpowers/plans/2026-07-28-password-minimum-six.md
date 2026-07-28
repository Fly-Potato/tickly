# Password Minimum Six Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将账号创建和修改密码的最短长度从 12 个字符调整为 6 个字符，并保持实现、测试和当前文档一致。

**Architecture:** 继续由 `app.core.security.validate_password` 作为设置新密码的唯一校验入口，账号服务和 CLI 无需增加重复校验。登录请求仍接受任意长度字符串并走统一凭据校验，避免改变认证失败语义。

**Tech Stack:** Python 3.13、pytest、FastAPI/Pydantic、pwdlib Argon2、Markdown

---

### Task 1: 以边界测试驱动最短长度调整

**Files:**
- Modify: `apps/api/tests/test_security.py`
- Modify: `apps/api/app/core/security.py`

- [x] **Step 1: 写入新的失败边界测试**

将原有 12 字符边界测试替换为：

```python
def test_password_requires_at_least_six_characters() -> None:
    with pytest.raises(InvalidPassword):
        validate_password("a" * 5)

    assert validate_password("a" * 6) == "a" * 6
```

- [x] **Step 2: 运行测试并确认按预期失败**

Run:

```powershell
mise exec -- uv run --project apps/api pytest apps/api/tests/test_security.py::test_password_requires_at_least_six_characters -q
```

Expected: FAIL；6 字符密码仍被当前 12 字符规则拒绝。

- [x] **Step 3: 写入最小实现**

在密码散列对象旁定义单一边界，并让校验和错误提示共用它：

```python
_MIN_PASSWORD_LENGTH = 6
_PASSWORD_HASH = PasswordHash.recommended()


def validate_password(value: str) -> str:
    """执行密码进入散列前的最小长度校验，不改变用户输入。"""

    if len(value) < _MIN_PASSWORD_LENGTH:
        raise InvalidPassword(f"密码至少需要 {_MIN_PASSWORD_LENGTH} 个字符")
    return value
```

- [x] **Step 4: 运行目标测试并确认通过**

Run:

```powershell
mise exec -- uv run --project apps/api pytest apps/api/tests/test_security.py::test_password_requires_at_least_six_characters -q
```

Expected: PASS。

### Task 2: 同步当前事实文档并执行 API 回归

**Files:**
- Modify: `README.md`
- Modify: `docs/roadmaps/2026-07-26-tickly-zero-to-one.md`
- Modify: `docs/superpowers/specs/2026-07-28-username-jwt-auth-design.md`

- [x] **Step 1: 更新用户可见说明**

在 README 账号管理段明确说明“密码最少 6 个字符”，并把路线图及认证设计中的“最少 12 个字符”改为“最少 6 个字符”。不修改登录接口 schema，也不增加 Web 表单限制。

- [x] **Step 2: 检查旧规则是否残留**

Run:

```powershell
rg -n "密码.*(最少|至少).*12|twelve_characters|len\(value\) < 12" README.md apps/api docs/roadmaps docs/superpowers/specs
```

Expected: 无匹配。

- [x] **Step 3: 运行 API 测试**

Run:

```powershell
mise exec -- pnpm test:api
```

Expected: 全部 API 测试通过；允许既有 Starlette `TestClient` 弃用警告，不允许新增错误或失败。

- [x] **Step 4: 审查变更范围**

Run:

```powershell
git diff --check
git status --short
git diff -- README.md apps/api/app/core/security.py apps/api/tests/test_security.py docs/roadmaps/2026-07-26-tickly-zero-to-one.md docs/superpowers/specs/2026-07-28-username-jwt-auth-design.md docs/superpowers/plans/2026-07-28-password-minimum-six.md
```

Expected: 只包含密码最短长度调整及本计划；不提交，等待用户明确授权。
