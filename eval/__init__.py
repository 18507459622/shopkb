"""shopkb 离线评测包。

模块分工：
    metrics.py  纯函数指标（数字命中、引文统计、分位数）—— 确定性那半边
    gate.py     门禁阈值与退出码 —— 让评测能红/绿，而不是只打印
    fakes.py    离线确定性替身 —— 无网络/不花 token 也能验证评测脚手架
    retrieval.py / e2e.py / sweep.py / features.py  四个可执行评测脚本
"""
