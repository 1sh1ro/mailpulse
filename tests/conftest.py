import os

# 宿主的 NO_PROXY 含 IPv6 方括号写法，httpx 解析会失败；测试进程内改为等价的合法写法。
os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"
os.environ["no_proxy"] = "localhost,127.0.0.1,::1"
