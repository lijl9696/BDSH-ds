# 自动采集服务

`collector` 是报表自动下载服务。它通过 Playwright 模拟浏览器操作平台后台，下载 Excel 后调用 `report-web` 的导入接口入库。

## 原则

- 只下载报表和调用导入接口，不直接写数据库。
- 第一次人工登录，保存 Playwright 登录态文件。
- 登录态失效时停止任务并写日志，不自动处理验证码或短信。
- 每个平台的页面地址和按钮选择器放在 `config/jobs.yml`。

## 目录

```text
collector/
  collector/
    browser_runner.py  # Playwright 下载流程
    import_client.py   # 调用 report-web 导入接口
    scheduler.py       # 定时任务
    cli.py             # 手动运行/保存登录态
  config/
    jobs.example.yml
```

## 配置任务

复制示例：

```bash
cp config/jobs.example.yml config/jobs.yml
```

把任务里的 URL 和 selector 改成真实平台页面：

```yaml
jobs:
  - code: meituan_daily
    enabled: true
    platform_code: meituan
    schedule_cron: "30 6 * * *"
    state_file: meituan_state.json
    report_page_url: "报表生成页面"
    trigger_selector: "text=生成报表"
    download_center_url: "下载中心页面"
    download_selector: "text=下载"
```

`trigger_selector` 和 `download_selector` 是 Playwright locator。常用写法：

```text
text=生成报表
text=下载
button:has-text("下载")
[data-testid="download"]
```

## 手动运行

进入容器后可以执行：

```bash
python -m collector.cli run meituan_daily
```

## 登录态

`login` 命令会打开浏览器让你人工登录，然后保存登录态：

```bash
python -m collector.cli login meituan_daily --login-url "登录页面 URL"
```

在 NAS 无图形界面时，更推荐先在本地可视化环境生成 `meituan_state.json`，再复制到 NAS 的 collector state 目录。

## 导入接口

下载成功后会自动调用：

```text
POST /imports/files
GET /imports/{batch_id}/preview
POST /imports/{batch_id}/commit
```

接口账号密码使用：

```text
IMPORT_AUTH_USERNAME
IMPORT_AUTH_PASSWORD
```

## 后续落地步骤

1. 拿到美团/抖音后台真实页面。
2. 用浏览器开发者工具或 Playwright 调试出按钮 selector。
3. 先手动执行 `python -m collector.cli run <job>`。
4. 下载和入库都成功后，再启用定时任务。
