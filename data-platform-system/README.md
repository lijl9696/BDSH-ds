# 团购数据指标系统

这是新一代本地化数据系统，和旧版 Python 桌面工具隔离。

## 目录

- `report-platform/`：FastAPI 后端、导入页面、平台配置、数据库结构。
- `collector/`：Playwright 自动采集服务，负责下载平台报表并调用导入接口。
- `deploy/`：Docker Compose 部署配置。

## 启动

在 `data-platform-system` 目录执行：

```bash
cp deploy/.env.example deploy/.env
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --build
```

访问：

- 导入页面：http://localhost:8000
- API 文档：http://localhost:8000/docs
- Metabase：http://localhost:3000
- Superset：http://localhost:8088（需要叠加 `deploy/docker-compose.superset.yml` 启动）

## 群晖部署

群晖生产环境推荐使用 GHCR 镜像部署 `report-web`，Postgres、BI 工具元数据和上传文件保存在群晖本地目录，方便后续频繁升级应用镜像而不影响业务数据。

部署说明见：

```text
deploy/SYNOLOGY_DEPLOY.md
```

生产 compose 文件：

```text
deploy/docker-compose.synology.yml
```

## 当前样本校验结果

已用真实样本校验：

- 美团门店级日全数据：识别第 2 行表头，172 行门店数据，29 个指标字段全部映射。
- 抖音周度门店报表：识别第 1 行表头，883 行门店数据，24 个指标字段全部映射。

校验命令：

```bash
python3 report-platform/tools/validate_sample_structure.py --platform meituan --file /path/to/meituan.xlsx
python3 report-platform/tools/validate_sample_structure.py --platform douyin --file /path/to/douyin.xlsx
```

## 下一步联调

1. 在有 Docker 的机器或群晖上启动服务。
2. 打开 `http://localhost:8000` 上传美团样本。
3. 检查预览里的字段、门店数、指标值数量。
4. 选择 `保留版本` 确认入库。
5. 用抖音样本重复上述流程。
6. 打开 Metabase 或 Superset 连接 PostgreSQL，开始做第一批看板。
