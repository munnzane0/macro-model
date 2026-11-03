# 宏观模型 · 企业回报 VS 资金成本

复刻抖音「量化舵」一分钟宏观模型：企业回报（沪深300全收益指数）与资金成本（10年期国债持有回报）双线对比，日频数据，2005年至今。

## 文件

- `index.html` — H5 交互页面（ECharts 双线图 + 关键时点标注），fetch `data.json` 加载数据
- `fetch_data.py` — 数据更新脚本（增量/全量），每日收盘后运行
- `data.json` — 最新数据（含事件标注点）
- `.github/workflows/update.yml` — 每日 23:30(北京时间) 自动更新 data.json 并推送

## 数据口径

- 红线：中证指数官网 H00300（沪深300全收益指数），2005-01-01 起日频
- 蓝线：中债登 10年期国债收益率，按修正久期 8.5 反推日频持有回报，2005年初=1000 归一化
- 事件标注：各时期风险溢价（回报/成本比）阶段性低点

## 本地运行

```bash
pip install -r requirements.txt
python fetch_data.py          # 增量更新
python fetch_data.py --full   # 全量重建
python -m http.server 8080    # 本地预览（需 HTTP 服务，file:// 无法 fetch data.json）
```

## 免责声明

仅为历史数据回顾与模型思路展示，不构成任何投资建议。
