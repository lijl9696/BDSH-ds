# 数据库字典

本文档记录 `report-platform` 当前 PostgreSQL 业务表结构，方便后续代码维护、数据核查和人工运维。

当前数据库连接默认见 `app/config.py`：`DATABASE_URL=postgresql+psycopg://tg_report:tg_report@postgres:5432/tg_report`。

## 关系概览

核心链路：

1. 平台报表上传后创建 `import_batches` 和 `import_files`。
2. 原始行进入 `raw_import_rows`。
3. 数值指标进入 `metric_values`，文本指标进入 `text_metric_values`。
4. 门店主数据维护在 `stores`。
5. 区域/负责人归属优先读取 `area_assignments`：先按省份 + 城市 + 门店名称精确匹配，再按省份 + 城市 + 门店性质粗匹配，无法判断时进入待确认。
6. 指标字段由 `metrics` 和 `field_mappings` 控制。

## platforms 平台表

用途：维护可导入的平台。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | SERIAL PK | 主键 |
| code | VARCHAR(64) UNIQUE | 平台代码，如 `meituan`、`douyin` |
| name | VARCHAR(128) | 平台名称 |
| enabled | BOOLEAN | 是否启用，默认 `TRUE` |

## stores 门店主数据表

用途：记录平台报表识别到的门店基础信息和当前归属。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | SERIAL PK | 主键 |
| store_code | VARCHAR(128) UNIQUE | 门店编码；如果报表没有独立编码，可能使用门店名称 |
| name | VARCHAR(255) | 门店名称 |
| province | VARCHAR(128) | 所在省份 |
| city | VARCHAR(128) | 所在城市 |
| region | VARCHAR(128) | 大区 |
| owner | VARCHAR(128) | 负责人 |
| status | VARCHAR(32) | 状态，默认 `active` |
| store_type | VARCHAR(32) | 门店性质：`direct` 直营、`franchise` 加盟、`unknown` 未知 |
| assignment_status | VARCHAR(32) | 归属状态：`confirmed` 已确认、`auto` 自动匹配、`review` 待确认、`unconfigured` 未配置 |
| assignment_source | VARCHAR(64) | 归属来源，如 `manual_store`、`store_name`、`city_type`、`city_default`、`city_conflict` |
| assignment_confidence | INTEGER | 匹配置信度，人工确认通常为 100 |
| assignment_note | TEXT | 匹配说明或人工备注 |
| aliases | JSONB | 别名扩展，默认 `{}` |

索引：

| 索引 | 字段 | 用途 |
| --- | --- | --- |
| idx_stores_region_owner | region, owner | 按大区/负责人汇总 |
| idx_stores_province_city | province, city | 按省市查询门店 |
| idx_stores_assignment_status | assignment_status | 查询待确认/未配置门店 |

## area_assignments 区域配置表

用途：维护省市、门店级别的大区和负责人归属。当前门店配置上传写入此表。

匹配规则：

1. 如果配置行有 `store_name`，报表导入时优先按 `province + city + store_name` 精确匹配。
2. 如果没有命中门店级配置，再按 `province + city + store_type` 匹配城市级直营/加盟配置。
3. 如果城市只有一条 `store_type='all'` 的兜底配置，继续按城市兜底匹配。
4. 如果同城同时存在直营和加盟配置，但门店性质无法判断，系统不会硬套城市配置，会把 `stores.assignment_status` 标为 `review`，等待人工确认。
5. 匹配时会做标准化：省市会去掉末尾行政后缀，如 `河南省` 和 `河南` 视为同一值；门店名会统一中英文括号和空格，如 `彭世修脚(龙子湖直营店)` 和 `彭世修脚（龙子湖直营店）` 视为同一值。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | SERIAL PK | 主键 |
| province | VARCHAR(128) | 所在省份 |
| city | VARCHAR(128) | 所在城市 |
| store_name | VARCHAR(255) | 门店名称；空字符串表示城市兜底配置 |
| store_type | VARCHAR(32) | 配置适用门店性质：`all` 全部、`direct` 直营、`franchise` 加盟 |
| region | VARCHAR(128) | 大区 |
| owner | VARCHAR(128) | 负责人 |
| enabled | BOOLEAN | 是否启用 |
| updated_at | TIMESTAMPTZ | 更新时间 |

约束和索引：

| 名称 | 字段 | 说明 |
| --- | --- | --- |
| uq_area_assignment_province_city_store_type | province, city, store_name, store_type | 同一省市同一门店同一性质只能有一条配置 |
| idx_area_assignments_lookup | province, city, store_name, store_type | 导入报表时快速匹配归属 |

常用运维 SQL：

```sql
-- 查看区域配置数量
SELECT COUNT(*) FROM area_assignments;

-- 清空区域配置，保留表结构
TRUNCATE TABLE area_assignments RESTART IDENTITY;

-- 查看某城市的城市兜底和门店级配置
SELECT province, city, store_name, store_type, region, owner
FROM area_assignments
WHERE province = '河南省' AND city = '郑州市'
ORDER BY store_name, store_type;

-- 查看待人工确认的门店
SELECT store_code, name, province, city, store_type, region, owner, assignment_source, assignment_note
FROM stores
WHERE assignment_status IN ('review', 'unconfigured')
ORDER BY province, city, name;
```

## store_assignments 门店配置表

用途：预留的门店级配置表。当前主流程未启用，历史代码保留了导入和同步能力。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | SERIAL PK | 主键 |
| platform_code | VARCHAR(64) | 平台代码 |
| province | VARCHAR(128) | 所在省份 |
| city | VARCHAR(128) | 所在城市 |
| region | VARCHAR(128) | 大区 |
| owner | VARCHAR(128) | 负责人 |
| store_name | VARCHAR(255) | 门店名称 |
| updated_at | TIMESTAMPTZ | 更新时间 |

约束和索引：

| 名称 | 字段 | 说明 |
| --- | --- | --- |
| uq_store_assignment_platform_name | platform_code, store_name | 同平台同门店只能有一条配置 |
| idx_store_assignments_lookup | platform_code, store_name | 按平台和门店查配置 |

## metrics 指标字典表

用途：维护系统识别和汇总的指标定义。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | SERIAL PK | 主键 |
| code | VARCHAR(128) UNIQUE | 指标代码 |
| name | VARCHAR(255) | 指标名称 |
| value_type | VARCHAR(32) | 指标类型，默认 `number` |
| unit | VARCHAR(64) | 单位 |
| aggregation | VARCHAR(64) | 聚合方式，默认 `sum` |
| enabled | BOOLEAN | 是否启用 |
| description | TEXT | 指标说明 |

## field_mappings 字段映射表

用途：维护平台报表源字段到系统指标的映射。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | SERIAL PK | 主键 |
| platform_code | VARCHAR(64) | 平台代码 |
| source_field | VARCHAR(255) | 报表原始字段名 |
| metric_code | VARCHAR(128) FK | 关联 `metrics.code` |
| data_type | VARCHAR(32) | 数据类型，默认 `number`；文本指标为 `text` |
| clean_rule | JSONB | 清洗规则扩展，默认 `{}` |
| enabled | BOOLEAN | 是否启用 |

约束：

| 名称 | 字段 | 说明 |
| --- | --- | --- |
| uq_field_mapping_platform_source | platform_code, source_field | 同平台同源字段只能映射一次 |

## import_batches 导入批次表

用途：记录每次报表导入任务。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | SERIAL PK | 主键 |
| platform_code | VARCHAR(64) | 平台代码 |
| period_start | DATE | 数据周期开始 |
| period_end | DATE | 数据周期结束 |
| source_type | VARCHAR(32) | 来源类型，默认 `file` |
| status | VARCHAR(32) | 状态，默认 `pending` |
| duplicate_policy | VARCHAR(32) | 重复策略：`skip`、`overwrite`、`version` |
| import_options | JSONB | 导入参数扩展 |
| row_count | INTEGER | 原始行数 |
| warning_count | INTEGER | 警告行数 |
| created_at | TIMESTAMPTZ | 创建时间 |

## import_files 导入文件表

用途：记录批次上传的文件。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | SERIAL PK | 主键 |
| batch_id | INTEGER FK | 关联 `import_batches.id` |
| filename | VARCHAR(255) | 原始文件名 |
| storage_path | VARCHAR(500) | 服务端保存路径 |
| sha256 | VARCHAR(64) | 文件哈希 |
| uploaded_at | TIMESTAMPTZ | 上传时间 |

## raw_import_rows 原始行表

用途：保存报表原始行，便于追溯和排错。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | SERIAL PK | 主键 |
| batch_id | INTEGER FK | 关联 `import_batches.id` |
| row_number | INTEGER | 文件内行号 |
| raw_data | JSONB | 原始行数据 |
| normalized_keys | JSONB | 标准化后的关键字段，如日期和门店编码 |
| warning | TEXT | 行级警告 |

## metric_values 数值指标明细表

用途：保存数值型指标明细。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | SERIAL PK | 主键 |
| batch_id | INTEGER FK | 关联 `import_batches.id` |
| metric_date | DATE | 指标日期 |
| platform_code | VARCHAR(64) | 平台代码 |
| store_code | VARCHAR(128) | 门店编码 |
| metric_code | VARCHAR(128) FK | 关联 `metrics.code` |
| value | NUMERIC(20,4) | 指标值 |
| dimensions | JSONB | 维度扩展，默认 `{}` |
| dimension_hash | VARCHAR(64) | 维度哈希，默认 `default` |
| version | INTEGER | 版本号 |
| is_active | BOOLEAN | 是否当前有效版本 |
| created_at | TIMESTAMPTZ | 创建时间 |

约束和索引：

| 名称 | 字段 | 说明 |
| --- | --- | --- |
| uq_metric_value_identity | metric_date, platform_code, store_code, metric_code, dimension_hash, version | 指标版本唯一 |
| idx_metric_values_lookup | metric_date, platform_code, store_code, metric_code | 仅索引 `is_active=TRUE` 的当前有效数据 |

## text_metric_values 文本指标明细表

用途：保存文本型指标明细，如牌级、等级、状态等。

字段和 `metric_values` 基本一致，区别是：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| value | VARCHAR(255) | 文本指标值 |

约束和索引：

| 名称 | 字段 | 说明 |
| --- | --- | --- |
| uq_text_metric_value_identity | metric_date, platform_code, store_code, metric_code, dimension_hash, version | 文本指标版本唯一 |
| idx_text_metric_values_lookup | metric_date, platform_code, store_code, metric_code | 仅索引 `is_active=TRUE` 的当前有效数据 |

## derived_metric_rules 派生指标规则表

用途：维护派生指标计算规则。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | SERIAL PK | 主键 |
| metric_code | VARCHAR(128) UNIQUE FK | 关联 `metrics.code` |
| expression | TEXT | 表达式 |
| numerator_metric | VARCHAR(128) | 分子指标 |
| denominator_metric | VARCHAR(128) | 分母指标 |
| enabled | BOOLEAN | 是否启用 |

## report_presets 报表预设表

用途：维护报表/看板预设配置。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | SERIAL PK | 主键 |
| code | VARCHAR(128) UNIQUE | 预设代码 |
| name | VARCHAR(255) | 预设名称 |
| config | JSONB | 预设配置 |
| enabled | BOOLEAN | 是否启用 |

## 运维备注

- 门店配置上传入口当前只要求一个 sheet：`区域配置`。
- `区域配置` 必填字段：`所在省份`、`所在城市`、`大区`、`负责人`。
- `区域配置` 可选字段：`门店名称`、`门店性质`。`门店名称` 填写后表示门店级覆盖；留空表示城市级配置。`门店性质` 可填 `直营`、`加盟`、`全部`，不填默认 `全部`。
- 同一城市同时存在直营和加盟时，建议至少维护两条城市级配置，或对特殊门店维护门店级配置；系统会把无法判断的门店标记为 `review`，可在前端“门店配置”弹窗里查询并人工保存。
- 人工排查门店归属时，注意源报表和配置表可能存在省份后缀、中英文括号、空格差异；应用匹配时会标准化，但直接 SQL 精确查询时仍要留意原始写法。
- 如需重新上传区域配置，可先清空 `area_assignments`，不需要清空 `stores` 或指标明细表；上传后点击“重新匹配”可按最新配置刷新已有门店。
- 导入指标明细时，重复数据由 `duplicate_policy` 控制：跳过、覆盖或新版本。
