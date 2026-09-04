"""认证 schema 输入校验（白名单正则）单元测试。"""
import pytest
from pydantic import ValidationError

from app.modules.auth.schemas import LoginRequest, RegisterRequest

# ---- 用户名白名单：只允许 字母/数字/下划线/常用汉字，3-64 位 ----

@pytest.mark.parametrize(
    "username",
    ["admin", "user_123", "张三丰", "a1_中文", "abc"],
)
def test_username_valid(username):
    assert LoginRequest(username=username, password="123456").username == username


@pytest.mark.parametrize(
    "username",
    [
        "admin' OR '1'='1",  # SQL 注入载荷（引号）
        "a; DROP TABLE users;--",  # 分号 / 连字符
        "a b",  # 空白
        "<script>",  # 尖括号（XSS）
        "user-name",  # 连字符
        "user.name",  # 点号
        "ab",  # 过短（<3）
        "a" * 65,  # 过长（>64）
    ],
)
def test_username_invalid(username):
    with pytest.raises(ValidationError):
        LoginRequest(username=username, password="123456")


# ---- 密码：6-128 位，禁止控制字符，不能全为空白 ----

@pytest.mark.parametrize("password", ["123456", "a" * 128, "密码abc123"])
def test_password_valid(password):
    assert LoginRequest(username="admin", password=password).password == password


@pytest.mark.parametrize(
    "password",
    [
        "12345",  # 过短（<6）
        "   ",  # 全空白
        "a" * 129,  # 过长（>128）
        "abc\n123",  # 控制字符（换行）
    ],
)
def test_password_invalid(password):
    with pytest.raises(ValidationError):
        LoginRequest(username="admin", password=password)


def test_register_uses_same_username_rules():
    with pytest.raises(ValidationError):
        RegisterRequest(username="bad name!", password="123456")
