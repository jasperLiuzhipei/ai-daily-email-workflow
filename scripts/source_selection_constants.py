"""Source selection constants."""
from __future__ import annotations
from pathlib import Path
import re
from datetime import timedelta
from datetime import timezone


TZ = timezone(timedelta(hours=8))


DEFAULT_FEED_URL = "https://aihot.news/feed.xml"


DEFAULT_PAPER_FEED_URL = "https://aihot.news/feed/category/paper.xml"


DEFAULT_ALL_FEED_URL = "https://aihot.news/feed/all.xml"


DEFAULT_DAILY_URL = "https://aihot.news/daily"


DEFAULT_BESTBLOGS_AI_FEED_URL = "https://www.bestblogs.dev/en/feeds/rss?category=ai&minScore=90"


DEFAULT_BESTBLOGS_MAX_ITEMS = 30


DEFAULT_SEND_LEDGER = Path("daily_runs") / "send_ledger.json"


WORKFLOW_DIR = Path(__file__).resolve().parents[1]


DEFAULT_SOURCE_REGISTRY = WORKFLOW_DIR / "config" / "source_registry.json"


DEFAULT_SELECTION_POLICY = WORKFLOW_DIR / "config" / "selection_policy.json"


DEFAULT_AUDIENCE_PROFILES = WORKFLOW_DIR / "config" / "audience_profiles.json"


RESEARCH_SOURCE_HOSTS = {
    "anthropic.com",
    "arxiv.org",
    "blog.google",
    "deepmind.google",
    "machinelearning.apple.com",
    "microsoft.com",
    "nist.gov",
    "openai.com",
    "research.google",
    "www.anthropic.com",
    "www.lmsys.org",
    "www.nist.gov",
}


PAPER_PRIMARY_HOSTS = RESEARCH_SOURCE_HOSTS | {
    "aclanthology.org",
    "doi.org",
    "huggingface.co",
    "openreview.net",
    "papers.nips.cc",
    "proceedings.mlr.press",
}


SOCIAL_OR_AGGREGATOR_HOSTS = {
    "aihot.virxact.com",
    "bestblogs.dev",
    "news.ycombinator.com",
    "twitter.com",
    "x.com",
}


SECONDARY_NEWS_HOSTS = {
    "every.to",
    "ithome.com",
    "marktechpost.com",
    "the-decoder.com",
}


COMMENTARY_ONLY_DISCOVERY_HOSTS = {"every.to"}


RESEARCH_ONLY_WINDOW_STRATEGIES = {
    "paper_lookback",
    "direct_research_lookback",
    "content_reclassified_research_lookback",
}


SOURCE_QUALITY_TIERS = {"primary", "verified_social", "discovery", "resolution_only"}


CANONICAL_WWW_HOSTS = {
    # These first-party publishers serve the same editorial resource on both
    # hostnames. Treating the variants as separate URLs lets one study enter
    # through both discovery and direct-source paths.
    "anthropic.com",
    "openai.com",
    "ai.meta.com",
    "huggingface.co",
}


CRYPTOGRAPHY_RESEARCH_KEYWORDS = [
    "密码学",
    "密码算法",
    "cryptographic",
    "cryptography",
    "格密码",
    "lattice",
    "密钥恢复",
    "key recovery",
    "签名方案",
    "signature scheme",
    "aes",
    "hawk",
]


NARROW_CRYPTOGRAPHY_MARKERS = [
    "加密弱点",
    "加密算法弱点",
    "密码分析",
    "cryptographic weaknesses",
    "候选方案",
    "candidate scheme",
    "七轮",
    "7 轮",
    "7-round",
    "轮数简化",
    "简化版",
    "变体",
    "格结构",
    "lattice structure",
    "数学方法",
]


APPLIED_AI_SECURITY_CONTEXT_KEYWORDS = [
    "claude code",
    "codex",
    "cursor",
    "权限",
    "用户数据",
    "数据泄露",
    "代码库",
    "仓库",
    "供应链",
    "提示注入",
    "prompt injection",
    "生产环境",
    "现网",
    "已部署",
    "漏洞已修复",
    "cve-",
    "zero-day",
]


RESEARCH_EVALUATION_KEYWORDS = [
    "联合评估",
    "官方评估",
    "独立评估",
    "技术评估",
    "研究评估",
    "评估报告",
    "技术报告",
    "研究报告",
    "研究所",
    "研究院",
    "实验结果",
    "数据集",
    "样本",
    "消融",
    "joint evaluation",
    "official evaluation",
    "independent evaluation",
    "technical evaluation",
    "technical report",
    "research report",
    "institute",
    "dataset",
    "ablation",
    "nist",
    "aisi",
    "caisi",
]


RESEARCH_ARTIFACT_ACTION_KEYWORDS = [
    "提出",
    "构建",
    "创建",
    "推出",
    "发布",
    "开源",
    "introduce",
    "introduces",
    "propose",
    "proposes",
    "construct",
    "release",
    "open-source",
]


RESEARCH_ARTIFACT_KEYWORDS = [
    "新基准",
    "基准套件",
    "评测框架",
    "评估框架",
    "数据集",
    "benchmark",
    "bench",
    "evaluation framework",
    "dataset",
]


HTML_TAG_RE = re.compile(r"<[^>]+>")


URL_RE = re.compile(r"https?://[^\s<>()\"']+")


AIHOT_ITEM_RE = re.compile(r"aihot\.(?:virxact\.com|news)/items/([^/?#]+)")


AIHOT_DAILY_ITEM_RE = re.compile(r"""<a\b[^>]*\bhref\s*=\s*["']/items/([^"'?#]+)[^"']*["'][^>]*>(.*?)</a>""", re.S | re.I)


ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})(?:v\d+)?", re.I)


DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.I)


BESTBLOGS_RESOURCE_ID_RE = re.compile(r"/(?:en/)?(?:article|status|video)/([^/?#]+)", re.I)


BESTBLOGS_PRIMARY_URL_RE = re.compile(r'\\"url\\":\\"(https?://[^"\\]+)', re.I)


X_STATUS_ID_RE = re.compile(r"(?:x\.com/(?:[^/]+/)?status/|/(?:en/)?status/)([0-9]{8,})", re.I)


TECHNIQUE_KEYWORDS = [
    "writing-great-skills",
    "great skills",
    "prompt engineering",
    "提示词工程",
    "prompt 技巧",
    "prompt技巧",
    "调试生产提示词",
    "评估是唯一严谨方式",
    "方法论",
    "工作方法",
    "agentic loop",
    "agentic loops",
    "loops",
    "智能体循环",
    "eval",
    "评估技巧",
    "调试",
    "completion criterion",
    "完成标准",
    "leading word",
    "no-op",
    "progressive disclosure",
    "渐进式披露",
    "可预测工作流",
    "过程可预测",
    "skillgrade",
    "skill 测试",
    "skills 测试",
    "单元测试",
    "回归测试",
    "regression",
    "ci 原生",
]


FOUNDATION_MODEL_KEYWORDS = [
    "openai",
    "chatgpt",
    "gpt-",
    "gpt ",
    "claude",
    "anthropic",
    "gemini",
    "google ai",
    "通义",
    "通义千问",
    "千问",
    "qwen",
    "qwen3",
    "qwen-max",
    "qwen-agent",
    "qwen agent",
    "百炼",
    "dashscope",
    "deepseek",
    "glm",
    "chatglm",
    "z.ai",
    "智谱",
    "minimax",
    "海螺",
    "stepfun",
    "阶跃星辰",
    "kimi",
    "moonshot",
    "豆包",
    "doubao",
    "seed",
    "混元",
    "hunyuan",
    "llama",
    "meta ai",
    "grok",
    "xai",
]


FOUNDATION_MODEL_MAJOR_SIGNAL_KEYWORDS = [
    "大版本",
    "重大升级",
    "发布",
    "release",
    "推出",
    "上线",
    "开源",
    "权重",
    "api",
    "大模型",
    "基础模型",
    "agent 能力",
    "智能体能力",
    "managed agents",
    "安全",
    "漏洞",
    "隐私",
    "权限",
    "用户数据",
    "数据泄露",
    "数据争议",
    "攻击",
    "后门",
    "争议",
    "风险",
]


MARKET_ADOPTION_SIGNAL_KEYWORDS = [
    "收入",
    "营收",
    "年化收入",
    "arr",
    "盈利",
    "利润",
    "融资",
    "估值",
    "支出",
    "成本",
    "定价",
    "使用量",
    "使用份额",
    "token 份额",
    "用户增长",
    "付费",
    "采购",
    "revenue",
    "profit",
    "spend",
    "pricing",
    "usage share",
]


SECURITY_RISK_KEYWORDS = [
    "安全风险",
    "漏洞",
    "隐私",
    "权限",
    "用户数据",
    "数据泄露",
    "数据争议",
    "攻击",
    "恶意",
    "后门",
    "供应链",
    "隐藏代码",
    "完全控制",
    "审计",
]


CONTROVERSY_SIGNAL_KEYWORDS = [
    "争议",
    "爆料",
    "泄露",
    "丑闻",
    "封禁",
    "抵制",
    "质疑",
    "后门",
    "隐写",
    "隐写术",
    "steganography",
    "暗藏",
    "植入",
    "监控",
    "检测",
    "识别中国",
    "中国用户",
    "地域识别",
    "隐藏逻辑",
    "隐藏代码",
    "安全风险",
    "供应链",
    "信任危机",
]


EVENT_PRODUCT_PATTERNS = [
    ("chatgpt-work", ["chatgpt work"]),
    ("claude-code", ["claude code"]),
    ("codex-micro", ["codex micro"]),
    ("grok-build", ["grok build"]),
    ("openai-presence", ["openai presence"]),
]


EVENT_ACTION_PATTERNS = [
    ("security", ["安全事件", "漏洞", "入侵", "泄露", "sandbox", "security incident"]),
    ("system-prompt", ["system prompt", "系统提示", "提示词", "prompt 削减", "prompt 瘦身"]),
    ("open-source", ["开源", "open source", "open-source", "开放权重"]),
    ("model-release", ["模型发布", "新模型", "大版本", "model release", "weights"]),
    ("feature-launch", ["发布", "推出", "上线", "功能", "支持", "正式可用", "introducing", "launch"]),
]


TRUST_BOUNDARY_KEYWORDS = [
    "claude code",
    "cursor",
    "codex",
    "ai coding",
    "编程工具",
    "开发者工具",
    "agent",
    "智能体",
    "权限",
    "脚本",
    "仓库",
    "用户识别",
    "隐私",
    "安全",
    "审计",
    "可审计",
]


MILITARY_GEO_RISK_KEYWORDS = [
    "美军",
    "五角大楼",
    "国防部",
    "军方",
    "军事",
    "军用",
    "战争",
    "战场",
    "目标选择",
    "误炸",
    "伊朗",
    "以色列",
    "乌克兰",
    "palantir",
    "pentagon",
    "defense",
    "military",
    "battlefield",
    "targeting",
    "warfare",
    "terrorist",
    "terrorism",
    "weapon",
    "weapons",
    "博科圣地",
    "恐怖",
    "袭击",
    "武器",
]


RESEARCH_CATEGORY_LABELS = [
    "Alignment",
    "Economic Research",
    "Frontier Red Team",
    "Interpretability",
    "Science",
    "Societal Impacts",
]


NON_TECHNICAL_RESEARCH_CATEGORIES = {"Economic Research", "Societal Impacts"}


ANTHROPIC_NEWS_CATEGORY_LABELS = [
    "Announcements",
    "Case Study",
    "Company",
    "Engineering",
    "Features",
    "Policy",
    "Product",
    "Safety",
]


DATE_LABEL_RE = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+\d{1,2},\s+\d{4}\b"
)


JSON_LD_SCRIPT_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)


DATED_LINK_TIME_CARD_RE = re.compile(
    r"<h[1-6]\b[^>]*>\s*<a\b(?P<link_attrs>[^>]*)>(?P<title>.*?)</a>\s*</h[1-6]>"
    r"\s*<time\b(?P<time_attrs>[^>]*)>(?P<date_text>.*?)</time>",
    re.I | re.S,
)


HREF_ATTR_RE = re.compile(r"\bhref=[\"'](?P<value>[^\"']+)[\"']", re.I)


DATETIME_ATTR_RE = re.compile(r"\bdatetime=[\"'](?P<value>[^\"']+)[\"']", re.I)

