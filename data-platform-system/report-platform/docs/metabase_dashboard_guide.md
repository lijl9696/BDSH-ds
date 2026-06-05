# Metabase 仪表盘配置规范

本文档记录团购数据系统在 Metabase 里创建 SQL 卡片、绑定筛选器和选择字段的规范。

## 核心表

常用查询主要使用两张表：

| 用途 | 表 | 关键字段 |
| --- | --- | --- |
| 指标明细 | `metric_values` | `metric_date`、`platform_code`、`store_code`、`metric_code`、`value`、`is_active` |
| 门店维度 | `stores` | `store_code`、`name`、`province`、`city`、`region`、`owner` |

常用关联：

```sql
FROM metric_values
LEFT JOIN stores ON stores.store_code = metric_values.store_code
```

## 筛选器绑定字段

Metabase 仪表盘筛选器必须绑定到真实数据表字段，不能绑定 SQL 里显示出来的中文别名。

| 筛选器 | 变量名建议 | 变量类型 | 绑定表字段 |
| --- | --- | --- | --- |
| 日期 | `date_filter` | 字段筛选器 / Field Filter | `metric_values.metric_date` |
| 大区 | `region_filter` | 字段筛选器 / Field Filter | `stores.region` |
| 负责人 | `owner_filter` | 字段筛选器 / Field Filter | `stores.owner` |
| 门店 | `store_filter` | 字段筛选器 / Field Filter | `stores.name` |
| 城市 | `city_filter` | 字段筛选器 / Field Filter | `stores.city` |
| 省份 | `province_filter` | 字段筛选器 / Field Filter | `stores.province` |
| 平台 | `platform_filter` | 字段筛选器 / Field Filter | `metric_values.platform_code` |

## 字段筛选器 SQL 写法

字段筛选器由 Metabase 自动生成完整条件。SQL 里只写变量，不要手写字段名和等号。

正确：

```sql
[[AND {{date_filter}}]]
[[AND {{region_filter}}]]
[[AND {{store_filter}}]]
```

错误：

```sql
[[AND metric_values.metric_date = {{date_filter}}]]
[[AND stores.region = {{region_filter}}]]
```

错误原因：字段筛选器会自动展开成类似 `metric_values.metric_date = '2026-05-22'` 的条件。如果手写 `=`，最终 SQL 会变成重复条件，容易报 `syntax error at or near "="`。

## 表别名规则

为了让 Metabase 字段筛选器更稳定地识别绑定字段，SQL 卡片优先不要给 `metric_values` 和 `stores` 起短别名。

推荐：

```sql
FROM metric_values
LEFT JOIN stores ON stores.store_code = metric_values.store_code
```

不推荐用于字段筛选器卡片：

```sql
FROM metric_values mv
LEFT JOIN stores s ON s.store_code = mv.store_code
```

短别名不是一定不能用，但新卡片优先使用完整表名，减少绑定筛选器时的歧义。

## 门店经营明细示例

```sql
SELECT
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
  AND metric_values.platform_code = 'meituan'
  AND metric_values.metric_code IN (
    'paid_amount',
    'verified_amount',
    'verified_count',
    'verified_new_customer_count'
  )
  [[AND {{date_filter}}]]
  [[AND {{region_filter}}]]
  [[AND {{store_filter}}]]
GROUP BY
  COALESCE(stores.region, '未配置'),
  COALESCE(stores.owner, '未配置'),
  stores.name
ORDER BY 核销金额 DESC;
```

变量设置：

| 变量 | 类型 | 绑定字段 |
| --- | --- | --- |
| `date_filter` | 字段筛选器 | `metric_values.metric_date` |
| `region_filter` | 字段筛选器 | `stores.region` |
| `store_filter` | 字段筛选器 | `stores.name` |

仪表盘筛选器：

| 仪表盘筛选器 | 连接卡片变量 |
| --- | --- |
| 时间筛选器，可用单日或日期范围 | `date_filter` |
| 大区文本/分类筛选器 | `region_filter` |
| 门店文本/分类筛选器 | `store_filter` |

## 关于“未配置”

SQL 中的：

```sql
COALESCE(stores.region, '未配置') AS 大区
```

只是展示效果，真实字段 `stores.region` 仍然是空值。字段筛选器绑定 `stores.region` 时，可以筛选真实大区值，如 `郑北`、`郑东`、`华中`；但不能直接把展示出来的 `未配置` 当成真实字段值。

如果需要专门筛选未配置门店，建议单独做一张“未配置门店”卡片：

```sql
SELECT
  stores.name AS 门店名称,
  stores.province AS 省份,
  stores.city AS 城市,
  stores.region AS 大区,
  stores.owner AS 负责人
FROM stores
WHERE COALESCE(stores.region, '') = ''
   OR COALESCE(stores.owner, '') = ''
ORDER BY stores.province, stores.city, stores.name;
```

## 普通变量写法

如果不使用字段筛选器，而使用普通变量，才需要手写字段名和条件。

示例：

```sql
[[AND metric_values.metric_date >= {{start_date}}]]
[[AND metric_values.metric_date <= {{end_date}}]]
[[AND stores.region = {{region}}]]
```

普通变量适合简单临时查询；仪表盘联动筛选优先使用字段筛选器。
