# Mac 开发，交付 Windows 用户

PyInstaller 不能在 macOS 上直接可靠地交叉打包 Windows `.exe`。本项目使用 GitHub Actions 的 Windows 环境自动打包。

## 你需要做什么

1. 把项目推到 GitHub 仓库。
2. 打开 GitHub 仓库页面。
3. 进入 `Actions`。
4. 选择 `Build Windows App`。
5. 点击 `Run workflow`。
6. 等待任务完成。
7. 在任务页面下载 artifact：

```text
团购报表工具-Windows
```

下载后里面是：

```text
团购报表工具-Windows.zip
```

## 交付给最终用户

把 `团购报表工具-Windows.zip` 发给 Windows 用户。

用户只需要：

1. 解压 zip。
2. 打开 `团购报表工具` 文件夹。
3. 双击 `团购报表工具.exe`。

用户不需要安装：

- Python
- pandas
- openpyxl
- pillow
- PyInstaller
- 任何开发工具

## 固定目录

exe 同级目录会包含：

```text
配置表
assets
outputs
data
```

程序会固定读取：

```text
配置表\报表工具配置模板.xlsx
配置表\美团输出报表模板.xlsx
配置表\抖音输出报表模板.xlsx
```

程序会固定输出：

```text
outputs
data
```

程序图标可放在：

```text
assets\icons\app.png
```
