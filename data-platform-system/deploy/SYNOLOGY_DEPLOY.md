# 群晖部署说明

本文档用于把团购数据指标系统部署到群晖 Container Manager。推荐方式是：GitHub Actions 构建 `report-web` 镜像，群晖拉取镜像运行，数据库和上传文件保存在群晖目录。

## 部署结构

服务：

| 服务 | 镜像 | 数据保存位置 |
| --- | --- | --- |
| report-web | `ghcr.io/lijl9696/bdsh-ds-report-web:latest` | 上传文件挂载到 `${TG_REPORT_DATA_DIR}/uploads` |
| postgres | `postgres:16` | `${TG_REPORT_DATA_DIR}/postgres` |
| metabase | `metabase/metabase:v0.52.4` | `${TG_REPORT_DATA_DIR}/metabase` |
| backup | `postgres:16` | `${TG_REPORT_DATA_DIR}/backups` |

只要不删除 `${TG_REPORT_DATA_DIR}`，更新 `report-web` 镜像不会影响业务数据、Metabase 仪表盘、登录账号和上传文件。

## 首次部署

推荐把本系统单独放在 GitHub 仓库：

```text
https://github.com/lijl9696/BDSH-ds.git
```

如果从本机首次推送到新仓库，建议只提交 `data-platform-system/` 和 `.github/workflows/build-report-web-image.yml`，不要把旧版桌面工具的历史文件变更一起带进去。

示例：

```bash
git remote set-url origin git@github.com:lijl9696/BDSH-ds.git
git add data-platform-system .github/workflows/build-report-web-image.yml
git commit -m "Add data platform deployment"
git push -u origin main
```

如果当前工作区包含旧项目文件删除、移动或其他无关变更，先单独整理一个干净目录再推送更稳。

1. 在 GitHub 仓库确认 Actions 已成功生成镜像：

```text
ghcr.io/lijl9696/bdsh-ds-report-web:latest
```

2. 把仓库同步到群晖，例如放到：

```text
/volume1/docker/BDSH-ds
```

3. 进入部署目录：

```bash
cd /volume1/docker/BDSH-ds/data-platform-system/deploy
```

4. 复制环境变量文件：

```bash
cp .env.synology.example .env
```

5. 编辑 `.env`：

```text
POSTGRES_PASSWORD=换成一个足够长的密码
TG_REPORT_DATA_DIR=/volume1/docker/tg-report
REPORT_WEB_IMAGE=ghcr.io/lijl9696/bdsh-ds-report-web:latest
REPORT_WEB_PORT=8000
METABASE_PORT=3000
POSTGRES_PORT=5432
```

6. 创建数据目录：

```bash
mkdir -p /volume1/docker/tg-report/postgres
mkdir -p /volume1/docker/tg-report/metabase
mkdir -p /volume1/docker/tg-report/uploads
mkdir -p /volume1/docker/tg-report/backups
```

7. 启动：

```bash
docker-compose -f docker-compose.synology.yml up -d
```

8. 访问：

```text
report-web: http://群晖IP:8000
Metabase: http://群晖IP:3000
```

## 日常升级

本地改代码后推到 GitHub，Actions 会自动构建新镜像。群晖升级时执行：

```bash
cd /volume1/docker/BDSH-ds/data-platform-system/deploy
docker-compose -f docker-compose.synology.yml pull report-web
docker-compose -f docker-compose.synology.yml up -d report-web
```

这只会更新应用容器，不会删除 Postgres 数据、Metabase 数据和上传文件。

## 重要注意

不要执行：

```bash
docker-compose -f docker-compose.synology.yml down -v
```

`-v` 会删除 Docker volume。虽然本项目主要使用群晖目录挂载，但仍建议避免这个习惯，防止误删其他卷。

不要删除：

```text
${TG_REPORT_DATA_DIR}/postgres
${TG_REPORT_DATA_DIR}/metabase
${TG_REPORT_DATA_DIR}/uploads
${TG_REPORT_DATA_DIR}/backups
```

## 备份和恢复

备份服务会每天生成压缩 SQL：

```text
${TG_REPORT_DATA_DIR}/backups/tg_report_YYYY-MM-DD_HHMMSS.sql.gz
```

手动备份：

```bash
docker exec tg-report-postgres pg_dump -U tg_report tg_report | gzip > /volume1/docker/tg-report/backups/manual_$(date +%F_%H%M%S).sql.gz
```

恢复前建议先停应用：

```bash
docker-compose -f docker-compose.synology.yml stop report-web metabase backup
```

恢复数据库：

```bash
gunzip -c /volume1/docker/tg-report/backups/备份文件.sql.gz | docker exec -i tg-report-postgres psql -U tg_report -d tg_report
```

恢复后启动：

```bash
docker-compose -f docker-compose.synology.yml up -d
```

## GHCR 权限

如果群晖拉取镜像失败，先到 GitHub Package 页面确认镜像是否公开。

如果保持私有镜像，需要在群晖登录 GHCR：

```bash
docker login ghcr.io
```

用户名填 GitHub 用户名，密码填 GitHub Personal Access Token，Token 至少需要 `read:packages` 权限。
