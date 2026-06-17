# 团购数据指标系统

这是新一代本地化数据系统骨架，目标是替代固定 Excel/旧脚本式报表，建设可扩展的指标数据底座。

## 模块边界

- `report-web`：自研导入、指标、字段映射、汇总 API。
- `postgres`：长期数据存储。
- `metabase` / `superset`：BI 看板和自由分析。
- `backup`：数据库定时备份。

数据流：

```text
美团/抖音文件或未来 API
  -> 原始导入批次
  -> 字段映射
  -> 标准指标
  -> 指标明细长表
  -> 灵活汇总 API / BI 看板
```

## 当前已搭建

- PostgreSQL 表结构和种子指标。
- 指标字典 API。
- 字段映射 API，支持单条和批量配置。
- 文件上传、导入预览、确认入库、原始行保存、字段映射入库 API。
- 灵活汇总 API。
- 报表 preset 配置读取。
- NAS Docker Compose 部署骨架。

## 本地启动

在 `data-platform-system` 目录执行：

```bash
cp deploy/.env.example deploy/.env
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --build
```

访问：

- 导入 Web 页面: http://localhost:8000
- 导入/指标 API 文档: http://localhost:8000/docs
- Metabase: http://localhost:3000
- Superset: http://localhost:8088（需要叠加 `../deploy/docker-compose.superset.yml` 启动）

## 生产部署

群晖部署使用预构建镜像：

```text
ghcr.io/lijl9696/bdsh-ds-report-web:latest
```

部署和升级步骤见：

```text
../deploy/SYNOLOGY_DEPLOY.md
```

## 导入流程

第一版导入分三步，避免重复数据直接污染正式指标：

1. `POST /imports/files`
   - 上传美团/抖音 Excel 或 CSV。
   - 创建导入批次，只保存文件和导入选项。
2. `GET /imports/{batch_id}/preview`
   - 预览识别到的日期字段、门店字段、映射字段、未映射字段、异常行、重复指标数量。
3. `POST /imports/{batch_id}/commit`
   - 确认入库。
   - 默认使用 `skip`，已存在的同日期、同平台、同门店、同指标数据不重复写入。
   - `overwrite` 用于修正数据，会更新当前有效记录。
   - `version` 仅用于需要审计历史版本的场景，会保留旧版本，但默认看板只统计最新 active 版本。

默认字段映射见：

```text
config/default_field_mappings.yml
```

平台表头识别和字段别名见：

```text
config/platform_profiles.yml
```

Metabase SQL 卡片和仪表盘筛选器配置规范见：

```text
docs/metabase_dashboard_guide.md
```

Superset / Metabase 建模时的数据表连接关系见：

```text
docs/data_model_relationships.md
```

已分析过的平台样本结构见：

```text
docs/samples/
```

## 下一步

第一版还需要继续补：

- 派生指标公式已支持常见分子/分母类指标，后续补更复杂公式。
- 补平台默认字段映射导入模板。
- Web 管理页面。
- BI 默认看板初始化。

## 样本结构校验

不依赖 pandas/openpyxl，可直接校验平台导出表是否能被当前 profile 识别：

```bash
python3 tools/validate_sample_structure.py --platform meituan --file /path/to/meituan.xlsx
python3 tools/validate_sample_structure.py --platform douyin --file /path/to/douyin.xlsx
```
