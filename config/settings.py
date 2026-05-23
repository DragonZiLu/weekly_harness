"""
# 红利周期投资周度评估系统 - 配置
"""
import os
from pathlib import Path
from dataclasses import dataclass, field
from dotenv import load_dotenv

# 加载 .env 文件（tushare token 等敏感信息存放在此）
load_dotenv(Path(__file__).parent.parent / ".env")

# ===== 项目根目录 =====
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = PROJECT_ROOT / "logs"

# 确保目录存在
for _dir in [DATA_DIR, LOG_DIR]:
    _dir.mkdir(parents=True, exist_ok=True)


# ===== tushare 配置 =====
@dataclass
class TushareConfig:
    token: str = field(default_factory=lambda: os.getenv("TUSHARE_TOKEN", ""))
    # 每分钟请求限制（pro 用户约 200-500 次/分钟，根据积分档位调整）
    requests_per_minute: int = 200
    # 请求重试次数
    max_retries: int = 3
    # 重试等待秒数
    retry_delay: float = 2.0


# 全局配置实例
tushare_cfg = TushareConfig()
