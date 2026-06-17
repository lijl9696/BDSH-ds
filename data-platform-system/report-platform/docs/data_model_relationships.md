# 数据表连接关系和 BI 建模说明

本文档用于在 Superset / Metabase 中建立数据源、数据集、图表和筛选器。完整字段字典见 `database_dictionary.md`，这里重点记录表之间怎么连、看板常用字段从哪里来。

## 核心结论

做经营看板时，优先使用这条主链路：

```sql
metric_values
LEFT JOIN stores ON stores.store_code = metric_values.store_code
LEFT JOIN metrics ON metrics.code = metric_values.metric_code
```

如果要看文本类指标，比如经营评分牌级、星级、等级等，用：

```sql
text_metric_values
LEFT JOIN stores ON stores.store_code = text_metric_values.store_code
LEFT JOIN metrics ON metrics.code = text_metric_values.metric_code
```

看板筛选器常用绑定字段：

| 筛选器 | 绑定表字段 |
| --- | --- |
| 日期 | `metric_values.metric_date` 或 `text_metric_values.metric_date` |
| 平台 | `metric_values.platform_code` 或 `text_metric_values.platform_code` |
| 大区 | `stores.region` |
| 负责人 | `stores.owner` |
| 门店 | `stores.name` |
| 省份 | `stores.province` |
| 城市 | `stores.city` |

## 表分层

| 层级 | 表 | 用途 | 是否常用于 BI |
| --- | --- | --- | --- |
| 事实表 | `metric_values` | 数值指标明细，如下单金额、核销金额、订单数 | 是，最常用 |
| 事实表 | `text_metric_values` | 文本指标明细，如牌级、等级、星级 | 是，做牌级/等级分析时用 |
| 维度表 | `stores` | 门店名称、省市、大区、负责人、门店性质 | 是，所有门店维度都从这里取 |
| 字典表 | `metrics` | 指标编码、指标中文名、单位、聚合方式 | 是，解释指标含义时用 |
| 配置表 | `field_mappings` | 平台原始字段到系统指标编码的映射 | 运维用，BI 一般不直接连 |
| 配置表 | `area_assignments` | 区域/负责人配置来源 | 运维用，BI 一般不直接连 |
| 导入追溯 | `import_batches` | 导入批次、周期、来源、重复策略 | 排错用 |
| 导入追溯 | `import_files` | 上传文件记录 | 排错用 |
| 导入追溯 | `raw_import_rows` | 原始报表行 JSON | 排错用，不建议直接做图 |
| 规则表 | `derived_metric_rules` | 派生指标规则 | 运维用 |
| 预设表 | `report_presets` | 报表预设配置 | 系统用 |

## 主要连接关系

| 左表 | 字段 | 右表 | 字段 | 关系说明 |
| --- | --- | --- | --- | --- |
| `metric_values` | `store_code` | `stores` | `store_code` | 数值指标关联门店维度 |
| `metric_values` | `metric_code` | `metrics` | `code` | 数值指标关联指标字典 |
| `metric_values` | `batch_id` | `import_batches` | `id` | 指标追溯到导入批次 |
| `text_metric_values` | `store_code` | `stores` | `store_code` | 文本指标关联门店维度 |
| `text_metric_values` | `metric_code` | `metrics` | `code` | 文本指标关联指标字典 |
| `text_metric_values` | `batch_id` | `import_batches` | `id` | 文本指标追溯到导入批次 |
| `import_files` | `batch_id` | `import_batches` | `id` | 文件归属导入批次 |
| `raw_import_rows` | `batch_id` | `import_batches` | `id` | 原始行归属导入批次 |
| `field_mappings` | `metric_code` | `metrics` | `code` | 原始字段映射到指标字典 |

注意：`area_assignments` 不直接和 `stores` 做固定外键。它是配置来源表，系统会根据省份、城市、门店名称、门店性质，把匹配结果写回 `stores.region`、`stores.owner`、`stores.assignment_status` 等字段。BI 查询时一般直接用 `stores` 的当前结果。

## 字段映射在哪里

字段映射有两处：

| 类型 | 位置 | 说明 |
| --- | --- | --- |
| 数据库表 | `field_mappings` | 当前运行时生效的字段映射 |
| 默认配置文件 | `config/default_field_mappings.yml` | 新库初始化或维护默认映射时使用 |

查看数据库里的字段映射：

```sql
SELECT
  field_mappings.platform_code AS 平台,
  field_mappings.source_field AS 原始字段,
  field_mappings.metric_code AS 指标编码,
  metrics.name AS 指标名称,
  field_mappings.data_type AS 数据类型,
  field_mappings.enabled AS 是否启用
FROM field_mappings
LEFT JOIN metrics ON metrics.code = field_mappings.metric_code
ORDER BY field_mappings.platform_code, field_mappings.source_field;
```

## Superset 建模建议

建议先建三个数据集：

| 数据集名称 | 主表 | 连接 | 用途 |
| --- | --- | --- | --- |
| 数值指标明细 | `metric_values` | 左连接 `stores`、`metrics` | 金额、订单数、新客数、评价数等大多数图表 |
| 文本指标明细 | `text_metric_values` | 左连接 `stores`、`metrics` | 金牌/银牌/铜牌、星级、等级等文本分类 |
| 门店主数据 | `stores` | 不连接或按需连接 | 门店配置核查、未配置门店列表 |

### 数值指标明细数据集

主表：`metric_values`

左连接：

```text
metric_values.store_code = stores.store_code
metric_values.metric_code = metrics.code
```

常用字段：

| 展示名 | 来源字段 |
| --- | --- |
| 日期 | `metric_values.metric_date` |
| 平台 | `metric_values.platform_code` |
| 指标编码 | `metric_values.metric_code` |
| 指标值 | `metric_values.value` |
| 门店编码 | `stores.store_code` |
| 门店名称 | `stores.name` |
| 大区 | `stores.region` |
| 负责人 | `stores.owner` |
| 省份 | `stores.province` |
| 城市 | `stores.city` |
| 门店性质 | `stores.store_type` |
| 指标名称 | `metrics.name` |
| 指标单位 | `metrics.unit` |

过滤条件建议默认加：

```sql
metric_values.is_active = TRUE
```

### 文本指标明细数据集

主表：`text_metric_values`

左连接：

```text
text_metric_values.store_code = stores.store_code
text_metric_values.metric_code = metrics.code
```

常用字段：

| 展示名 | 来源字段 |
| --- | --- |
| 日期 | `text_metric_values.metric_date` |
| 平台 | `text_metric_values.platform_code` |
| 指标编码 | `text_metric_values.metric_code` |
| 指标文本值 | `text_metric_values.value` |
| 门店名称 | `stores.name` |
| 大区 | `stores.region` |
| 负责人 | `stores.owner` |
| 指标名称 | `metrics.name` |

过滤条件建议默认加：

```sql
text_metric_values.is_active = TRUE
```

## 常用指标编码

实际以 `metrics` 表为准。常用经营指标一般如下：

| 指标编码 | 含义 |
| --- | --- |
| `paid_amount` | 下单金额 |
| `verified_amount` | 核销金额 |
| `verified_count` | 核销订单数 / 核销数量 |
| `verified_new_customer_count` | 核销新客数 |
| `paid_order_count` | 下单订单数 |
| `new_review_count` | 新增评价数 |
| `new_positive_review_count` | 新增好评数 |
| `new_negative_review_count` | 新增差评数 |
| `business_score` | 经营评分 |
| `business_score_level` | 经营评分牌级，文本指标 |

如果不确定某个中文字段对应哪个指标编码，查 `field_mappings`：

```sql
SELECT platform_code, source_field, metric_code
FROM field_mappings
WHERE source_field LIKE '%核销%'
ORDER BY platform_code, source_field;
```

## BI 查询模板

按日期、大区、门店汇总数值指标：

```sql
SELECT
  metric_values.metric_date AS 日期,
  COALESCE(stores.region, '未配置') AS 大区,
  COALESCE(stores.owner, '未配置') AS 负责人,
  stores.name AS 门店名称,
  SUM(CASE WHEN metric_values.metric_code = 'paid_amount' THEN metric_values.value ELSE 0 END) AS 下单金额,
  SUM(CASE WHEN metric_values.metric_code = 'verified_amount' THEN metric_values.value ELSE 0 END) AS 核销金额,
  SUM(CASE WHEN metric_values.metric_code = 'verified_count' THEN metric_values.value ELSE 0 END) AS 核销订单数,
  SUM(CASE WHEN metric_values.metric_code = 'verified_new_customer_count' THEN metric_values.value ELSE 0 END) AS 核销新客数
FROM metric_values
LEFT JOIN stores ON stores.store_code = metric_values.store_code
WHERE metric_values.is_active = TRUE
GROUP BY
  metric_values.metric_date,
  COALESCE(stores.region, '未配置'),
  COALESCE(stores.owner, '未配置'),
  stores.name
ORDER BY metric_values.metric_date DESC, 核销金额 DESC;
```

门店配置核查：

```sql
SELECT
  stores.store_code AS 门店编码,
  stores.name AS 门店名称,
  stores.province AS 省份,
  stores.city AS 城市,
  stores.store_type AS 门店性质,
  stores.region AS 大区,
  stores.owner AS 负责人,
  stores.assignment_status AS 归属状态,
  stores.assignment_source AS 归属来源,
  stores.assignment_note AS 归属说明
FROM stores
ORDER BY stores.assignment_status DESC, stores.province, stores.city, stores.name;
```

## 文档位置

| 文档/配置 | 路径 |
| --- | --- |
| 数据库字典 | `data-platform-system/report-platform/docs/database_dictionary.md` |
| BI 表连接关系 | `data-platform-system/report-platform/docs/data_model_relationships.md` |
| Metabase 配置规范 | `data-platform-system/report-platform/docs/metabase_dashboard_guide.md` |
| 默认字段映射 | `data-platform-system/report-platform/config/default_field_mappings.yml` |
| 建表 SQL | `data-platform-system/report-platform/db/init/001_schema.sql` |
| 默认指标/映射种子 | `data-platform-system/report-platform/db/init/002_seed.sql` |
